import json
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from openai import OpenAI

from ..cache import Cache
from ..config import cfg
from ..function import get_function
from ..printer import MarkdownPrinter, Printer, TextPrinter
from ..role import DefaultRoles, SystemRole


class Handler:
    cache = Cache(int(cfg.get("CACHE_LENGTH")), Path(cfg.get("CACHE_PATH")))

    def __init__(self, role: SystemRole) -> None:
        self.client = OpenAI(
            base_url=cfg.get("OPENAI_BASE_URL"),
            api_key=cfg.get("OPENAI_API_KEY"),
            timeout=int(cfg.get("REQUEST_TIMEOUT")),
        )
        self.role = role

    @property
    def printer(self) -> Printer:
        use_markdown = "APPLY MARKDOWN" in self.role.role
        code_theme, color = cfg.get("CODE_THEME"), cfg.get("DEFAULT_COLOR")
        return MarkdownPrinter(code_theme) if use_markdown else TextPrinter(color)

    def make_messages(self, prompt: str) -> List[Dict[str, str]]:
        raise NotImplementedError

    def handle_function_call(
        self,
        messages: List[dict[str, Any]],
        name: str,
        arguments: str,
    ) -> Generator[str, None, None]:
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "function_call": {"name": name, "arguments": arguments},
            }
        )

        if messages and messages[-1]["role"] == "assistant":
            yield "\n"

        dict_args = json.loads(arguments)
        joined_args = ", ".join(f'{k}="{v}"' for k, v in dict_args.items())
        yield f"> @FunctionCall `{name}({joined_args})` \n\n"

        result = get_function(name)(**dict_args)
        if cfg.get("SHOW_FUNCTIONS_OUTPUT") == "true":
            yield f"```text\n{result}\n```\n"
        messages.append({"role": "function", "content": result, "name": name})

    @cache
    def get_completion(
        self,
        model: str,
        temperature: float,
        top_p: float,
        messages: List[Dict[str, Any]],
        functions: Optional[List[Dict[str, str]]],
    ) -> Generator[str, None, None]:
        name = arguments = ""
        is_shell_role = self.role.name == DefaultRoles.SHELL.value
        is_code_role = self.role.name == DefaultRoles.CODE.value
        is_dsc_shell_role = self.role.name == DefaultRoles.DESCRIBE_SHELL.value
        if is_shell_role or is_code_role or is_dsc_shell_role:
            functions = None

        for chunk in self.client.chat.completions.create(
            model=model,
            temperature=temperature,
            top_p=top_p,
            messages=messages,  # type: ignore
            functions=functions,  # type: ignore
            stream=True,
        ):
            delta = chunk.choices[0].delta  # type: ignore
            if delta.function_call:
                if delta.function_call.name:
                    name = delta.function_call.name
                if delta.function_call.arguments:
                    arguments += delta.function_call.arguments
            if chunk.choices[0].finish_reason == "function_call":  # type: ignore
                yield from self.handle_function_call(messages, name, arguments)
                yield from self.get_completion(
                    model, temperature, top_p, messages, functions, caching=False
                )
                return

            yield delta.content or ""

    def handle(
        self,
        prompt: str,
        model: str,
        temperature: float,
        top_p: float,
        caching: bool,
        functions: Optional[List[Dict[str, str]]] = None,
        **kwargs: Any,
    ) -> str:
        disable_stream = cfg.get("DISABLE_STREAMING") == "true"
        messages = self.make_messages(prompt.strip())
        generator = self.get_completion(
            model=model,
            temperature=temperature,
            top_p=top_p,
            messages=messages,
            functions=functions,
            caching=caching,
            **kwargs,
        )
        return self.printer(generator, not disable_stream)
