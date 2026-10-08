from __future__ import annotations

import math
from typing import Any

from ..credentials import credential_value
from ..errors import ConfigurationError, KeywordMovesError
from ..models import LLMRequest, LLMResult, PluginDescriptor


def _text_option(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        # Do not interpolate option values: they may be credentials.
        raise ConfigurationError(f"OpenAI {name} must be a non-empty string.")
    return value.strip()


def _integer_option(value: Any, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ConfigurationError(f"OpenAI {name} must be an integer >= {minimum}.")
    try:
        number = int(value)
    except ValueError:
        raise ConfigurationError(f"OpenAI {name} must be an integer >= {minimum}.") from None
    if number < minimum:
        raise ConfigurationError(f"OpenAI {name} must be an integer >= {minimum}.")
    return number


def _number_option(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int, str)):
        raise ConfigurationError(f"OpenAI {name} must be a finite number.")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise ConfigurationError(f"OpenAI {name} must be a finite number.") from None
    if not math.isfinite(number):
        raise ConfigurationError(f"OpenAI {name} must be a finite number.")
    return number


class OpenAILLM:
    """Hosted text generation through the optional official OpenAI Python SDK."""

    descriptor = PluginDescriptor(
        name="openai",
        summary="Generate text with OpenAI's hosted models through the Responses API.",
        capabilities=("generate", "remote-inference"),
        operations=("generate",),
    )
    default_model = "gpt-4.1-mini"

    def generate(self, request: LLMRequest) -> LLMResult:
        options = request.options
        # An explicitly supplied key wins, even when invalid; never fall back to
        # another account silently. Resolve the environment at call time.
        key = options.get("api_key") if "api_key" in options else credential_value("OPENAI_API_KEY")
        if key is None:
            raise ConfigurationError(
                "The OpenAI LLM plugin requires an API key. Set OPENAI_API_KEY or pass "
                "--openai-api-key with --llm openai."
            )
        api_key = _text_option(key, "API key")
        model = _text_option(options.get("model", self.default_model), "model")
        max_output_tokens = _integer_option(
            options.get("max_output_tokens", request.max_new_tokens), "max_output_tokens", 16
        )
        timeout = _number_option(options.get("timeout", 60.0), "timeout")
        if timeout <= 0:
            raise ConfigurationError("OpenAI timeout must be greater than zero.")
        max_retries = _integer_option(options.get("max_retries", 2), "max_retries", 0)
        if not isinstance(request.prompt, str) or not request.prompt.strip():
            raise ConfigurationError("OpenAI prompt must be a non-empty string.")

        client_options: dict[str, Any] = {
            "api_key": api_key,
            # Do not accidentally send this key to an inherited OPENAI_BASE_URL.
            "base_url": "https://api.openai.com/v1",
            "timeout": timeout,
            "max_retries": max_retries,
        }
        for name in ("organization", "project"):
            if name in options:
                client_options[name] = _text_option(options[name], name)
        parameters: dict[str, Any] = {
            "model": model,
            "input": request.prompt,
            "max_output_tokens": max_output_tokens,
            "store": False,
        }
        # Omit sampling/reasoning controls unless explicitly requested. Different
        # model families accept different controls; do not guess from the name.
        if "temperature" in options:
            temperature = _number_option(options["temperature"], "temperature")
            if not 0 <= temperature <= 2:
                raise ConfigurationError("OpenAI temperature must be between 0 and 2.")
            parameters["temperature"] = temperature
        if "reasoning_effort" in options:
            parameters["reasoning"] = {
                "effort": _text_option(options["reasoning_effort"], "reasoning_effort")
            }

        try:
            import openai
        except ImportError:
            raise ConfigurationError(
                "The OpenAI LLM plugin needs the optional SDK. "
                "Install it with: pip install 'keywordmoves[openai]'. "
                'From a checkout use: python -m pip install -e ".[openai]".'
            ) from None

        # A short-lived client avoids caching secrets or reusing a different
        # account's client. The context manager closes connections on errors too.
        try:
            with openai.OpenAI(**client_options) as client:
                response = client.responses.create(**parameters)
        except openai.AuthenticationError:
            raise ConfigurationError(
                "OpenAI authentication failed. Check OPENAI_API_KEY or --openai-api-key."
            ) from None
        except openai.PermissionDeniedError:
            raise ConfigurationError(
                "OpenAI denied access. Check the API key's project and model permissions."
            ) from None
        except openai.NotFoundError:
            raise ConfigurationError(
                "The OpenAI model was not found or is unavailable to this project. Check --model."
            ) from None
        except (openai.BadRequestError, openai.UnprocessableEntityError):
            raise ConfigurationError(
                "OpenAI rejected the request. Check --model, input length and llm_ options. "
                "The model must support text generation through the Responses API; "
                "temperature and reasoning_effort are model-dependent."
            ) from None
        except openai.RateLimitError:
            raise KeywordMovesError(
                "OpenAI rate limit or quota exceeded. Check API billing/limits and retry later."
            ) from None
        except openai.APITimeoutError:
            raise KeywordMovesError(
                "The OpenAI request timed out. Retry or increase --option llm_timeout=120."
            ) from None
        except openai.APIConnectionError:
            raise KeywordMovesError(
                "Could not connect to OpenAI. Check the network, proxy and TLS configuration."
            ) from None
        except openai.APIError:
            # Provider messages/bodies can echo credentials or private input.
            # Do not print them or chain their traceback into public errors.
            raise KeywordMovesError(
                "The OpenAI API request failed. Retry later or check the API service status."
            ) from None

        return self._result(response, request, model)

    def _result(self, response: Any, request: LLMRequest, model: str) -> LLMResult:
        for item in getattr(response, "output", None) or ():
            for part in getattr(item, "content", None) or ():
                if getattr(part, "type", None) == "refusal":
                    raise KeywordMovesError("OpenAI refused the request; no suggestions were returned.")
        status = getattr(response, "status", None)
        if status == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
            if reason == "max_output_tokens":
                raise KeywordMovesError(
                    "OpenAI reached the output-token limit; partial suggestions were discarded. "
                    "Increase --option llm_max_output_tokens=2048 (reasoning tokens also count)."
                )
            raise KeywordMovesError("OpenAI returned an incomplete response; no suggestions were used.")
        if status != "completed":
            raise KeywordMovesError("OpenAI did not complete the response; no suggestions were used.")
        text = getattr(response, "output_text", None)
        if not isinstance(text, str) or not text.strip():
            raise KeywordMovesError("OpenAI returned no text; no suggestions were used.")

        # Only explicitly selected, non-secret fields enter result metadata.
        metadata: dict[str, Any] = {
            "task": request.task,
            "api": "responses",
            "requested_model": model,
            "response_id": getattr(response, "id", None),
            "store": False,
        }
        usage = getattr(response, "usage", None)
        if usage is not None:
            metadata["usage"] = {
                name: value
                for name in ("input_tokens", "output_tokens", "total_tokens")
                if isinstance(value := getattr(usage, name, None), int)
                and not isinstance(value, bool)
            }
        return LLMResult(
            plugin=self.descriptor.name,
            model=getattr(response, "model", None) or model,
            text=text.strip(),
            metadata=metadata,
        )
