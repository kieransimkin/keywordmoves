from __future__ import annotations

from typing import Any

from ..errors import ConfigurationError
from ..models import LLMRequest, LLMResult, PluginDescriptor


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class HuggingFaceTransformersLLM:
    """Local text2text generation through Hugging Face Transformers and PyTorch."""

    descriptor = PluginDescriptor(
        name="huggingface-transformers",
        summary="Run a local Hugging Face text2text model through PyTorch.",
        capabilities=("generate", "local-inference"),
        operations=("generate",),
    )
    default_model = "Qwen/Qwen2.5-0.5B-Instruct"
    default_revision = "2b01de6d1108f9b2b5e46a726aa678a359b6c03b"

    def __init__(self) -> None:
        self._loaded: dict[
            tuple[str, str | None, str | None], tuple[Any, Any, Any, str, bool]
        ] = {}

    def _load(self, request: LLMRequest) -> tuple[Any, Any, Any, str, bool]:
        try:
            import torch
            from transformers import (
                AutoConfig,
                AutoModelForCausalLM,
                AutoModelForSeq2SeqLM,
                AutoTokenizer,
            )
        except ImportError as exc:
            raise ConfigurationError(
                "The Hugging Face LLM plugin needs the optional dependencies. "
                "Install KeywordMoves with: pip install 'keywordmoves[huggingface]'."
            ) from exc

        model_name = str(request.options.get("model", self.default_model))
        revision = request.options.get("revision")
        if revision is None and model_name == self.default_model:
            revision = self.default_revision
        cache_dir = request.options.get("cache_dir")
        key = (model_name, str(revision) if revision else None, str(cache_dir) if cache_dir else None)
        if key in self._loaded:
            return self._loaded[key]

        local_only = _as_bool(request.options.get("local_files_only"), False)
        common = {
            "revision": revision,
            "cache_dir": cache_dir,
            "local_files_only": local_only,
            "trust_remote_code": False,
        }
        common = {name: value for name, value in common.items() if value is not None}
        config = AutoConfig.from_pretrained(model_name, **common)
        tokenizer = AutoTokenizer.from_pretrained(model_name, **common)
        model_class = AutoModelForSeq2SeqLM if config.is_encoder_decoder else AutoModelForCausalLM
        model = model_class.from_pretrained(model_name, use_safetensors=True, **common)

        requested_device = str(request.options.get("device", "auto"))
        if requested_device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        elif requested_device in {"cpu", "cuda"}:
            device = requested_device
        else:
            raise ConfigurationError("Hugging Face device must be 'auto', 'cpu', or 'cuda'.")
        if device == "cuda" and not torch.cuda.is_available():
            raise ConfigurationError("CUDA was selected, but PyTorch reports that CUDA is unavailable.")
        model.to(device)
        model.eval()
        loaded = (torch, tokenizer, model, device, bool(config.is_encoder_decoder))
        self._loaded[key] = loaded
        return loaded

    def generate(self, request: LLMRequest) -> LLMResult:
        torch, tokenizer, model, device, is_encoder_decoder = self._load(request)
        model_name = str(request.options.get("model", self.default_model))
        max_input_tokens = int(request.options.get("max_input_tokens", 512))
        prompt = request.prompt
        if not is_encoder_decoder and getattr(tokenizer, "chat_template", None):
            prompt = tokenizer.apply_chat_template(
                [{"role": "user", "content": request.prompt}],
                tokenize=False,
                add_generation_prompt=True,
            )
        encoded = tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=max_input_tokens,
        )
        encoded = {key: value.to(device) for key, value in encoded.items()}
        with torch.inference_mode():
            output = model.generate(
                **encoded,
                max_new_tokens=request.max_new_tokens,
                do_sample=False,
                num_beams=int(request.options.get("num_beams", 1)),
            )
        generated = output[0]
        if not is_encoder_decoder:
            generated = generated[encoded["input_ids"].shape[-1] :]
        text = tokenizer.decode(generated, skip_special_tokens=True).strip()
        revision = request.options.get("revision")
        if revision is None and model_name == self.default_model:
            revision = self.default_revision
        return LLMResult(
            plugin=self.descriptor.name,
            model=model_name,
            text=text,
            metadata={
                "device": device,
                "task": request.task,
                "revision": revision,
                "architecture": "seq2seq" if is_encoder_decoder else "causal",
            },
        )
