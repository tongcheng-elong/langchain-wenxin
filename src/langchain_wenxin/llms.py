"""Wrapper around Baidu Wenxin APIs."""
import json
import logging
import time
import warnings
from typing import Any, Dict, Generator, List, Mapping, Optional, Tuple

import aiohttp
import requests
import sseclient
from langchain.callbacks.manager import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain.chat_models.base import BaseChatModel
from langchain.llms.base import LLM
from langchain.schema import (
    AIMessage,
    BaseMessage,
    ChatGeneration,
    ChatResult,
    HumanMessage,
)
from langchain.utils import get_from_dict_or_env
from pydantic import BaseModel, Extra, root_validator

logger = logging.getLogger(__name__)


class WenxinClient():
    WENXIN_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
    WENXIN_CHAT_URL = "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/{endpoint}"

    def __init__(self, baidu_api_key: str, baidu_secret_key: str,
                 request_timeout: Optional[int] = None):
        self.baidu_api_key = baidu_api_key
        self.baidu_secret_key = baidu_secret_key
        self.request_timeout = request_timeout

        self.access_token = ""
        self.access_token_expires = 0

    def completions_url(self, model: str) -> str:
        """Get the URL for the completions endpoint."""
        endpoint = "completions"
        if model in ["eb-instant", "ernie-bot-turbo"]:
            endpoint = "eb-instant"
        return self.WENXIN_CHAT_URL.format(endpoint=endpoint)

    def grant_token(self) -> str:
        """Grant access token from Baidu Cloud."""
        now_timestamp = int(time.time())
        if self.access_token and now_timestamp < self.access_token_expires:
            return self.access_token

        r = requests.get(
            url=self.WENXIN_TOKEN_URL,
            params={
                "grant_type": "client_credentials",
                "client_id": self.baidu_api_key,
                "client_secret": self.baidu_secret_key,
            },
            timeout=5,
        )
        r.raise_for_status()
        response = r.json()
        self.access_token = response["access_token"]
        self.access_token_expires = now_timestamp + response["expires_in"]
        return self.access_token

    async def async_grant_token(self) -> str:
        """Async grant access token from Baidu Cloud."""
        now_timestamp = int(time.time())
        if self.access_token and now_timestamp < self.access_token_expires:
            return self.access_token

        # Here we are using aiohttp to make the request.
        # It is used in a context manager fashion to ensure cleanup.
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url=self.WENXIN_TOKEN_URL,
                params={
                    "grant_type": "client_credentials",
                    "client_id": self.baidu_api_key,
                    "client_secret": self.baidu_secret_key,
                },
                timeout=5,
            ) as r:
                r.raise_for_status()
                response = await r.json()

        self.access_token = response["access_token"]
        self.access_token_expires = now_timestamp + response["expires_in"]
        return self.access_token

    @staticmethod
    def construct_message(prompt: str, history: List[Tuple[str, str]]) -> List[Any]:
        messages = []
        for human, ai in history:
            messages.append({"role": "user", "content": human})
            messages.append({"role": "assistant", "content": ai})
        messages.append({"role": "user", "content": prompt})
        return messages

    def completion(self, model: str, prompt: str, history: List[Tuple[str, str]], **params) -> Any:
        """Call out to Wenxin's generate endpoint.

        Args:
            model: The model to use.
            prompt: The prompt to pass into the model.
            **params: Additional parameters to pass to the API.

        Returns:
            The response generated by the model.
        """
        params["messages"] = self.construct_message(prompt, history)
        params["stream"] = False
        url = self.completions_url(model)
        logger.debug(f"call wenxin: url[{url}], params[{params}]")
        r = requests.post(
            url=url,
            params={"access_token": self.grant_token()},
            json=params,
            timeout=self.request_timeout,
        )
        r.raise_for_status()
        response = r.json()
        error_code = response.get("error_code", 0)
        if error_code != 0:
            error_msg = response.get("error_msg", "Unknown error")
            msg = f"call wenxin failed, error_code: {error_code}, error_msg: {error_msg}"
            raise Exception(msg)

        return response

    async def acompletion(self, model: str, prompt: str, history: List[Tuple[str, str]], **params) -> Any:
        """Async all out to Wenxin's generate endpoint.

        Args:
            model: The model to use.
            prompt: The prompt to pass into the model.
            **params: Additional parameters to pass to the API.

        Returns:
            The response generated by the model.
        """
        import aiohttp
        params["messages"] = self.construct_message(prompt, history)
        params["stream"] = False
        url = self.completions_url(model)
        logger.debug(f"async call wenxin: url[{url}], params[{params}]")

        # Here we are using aiohttp to make the request.
        # It is used in a context manager fashion to ensure cleanup.
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url=url,
                params={"access_token": await self.async_grant_token()},
                json=params,
                timeout=self.request_timeout,
            ) as r:
                r.raise_for_status()
                response = await r.json()

        error_code = response.get("error_code", 0)
        if error_code != 0:
            error_msg = response.get("error_msg", "Unknown error")
            msg = f"call wenxin failed, error_code: {error_code}, error_msg: {error_msg}"
            raise Exception(msg)

        return response

    def completion_stream(self, model: str, prompt: str,
                          history: List[Tuple[str, str]], **params) -> Generator:
        """Call out to Wenxin's generate endpoint.

        Args:
            model: The model to use.
            prompt: The prompt to pass into the model.
            **params: Additional parameters to pass to the API.

        Returns:
            Generator: The response generated by the model.
        """
        params["messages"] = self.construct_message(prompt, history)
        params["stream"] = True
        url = self.completions_url(model)
        logger.debug(f"call wenxin: url[{url}], params[{params}]")
        r = requests.post(
            url=self.completions_url(model),
            params={"access_token": self.grant_token()},
            json=params,
            timeout=self.request_timeout,
            stream=True,
        )
        r.raise_for_status()
        if not r.headers.get("Content-Type").startswith("text/event-stream"):
            response = r.json()
            error_code = response.get("error_code", 0)
            if error_code != 0:
                error_msg = response.get("error_msg", "Unknown error")
                msg = f"call wenxin failed, error_code: {error_code}, error_msg: {error_msg}"
                raise Exception(msg)
            return response

        client = sseclient.SSEClient(r)
        for event in client.events():
            data = json.loads(event.data)
            yield data

    async def acompletion_stream(self, model: str, prompt: str,
                          history: List[Tuple[str, str]], **params) -> Generator:
        """Async call out to Wenxin's generate endpoint.

        Args:
            model: The model to use.
            prompt: The prompt to pass into the model.
            **params: Additional parameters to pass to the API.

        Returns:
            Generator: The response generated by the model.
        """
        params["messages"] = self.construct_message(prompt, history)
        params["stream"] = True
        url = self.completions_url(model)
        logger.debug(f"call wenxin: url[{url}], params[{params}]")

        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url=self.completions_url(model),
                params={"access_token": await self.async_grant_token()},
                json=params,
            ) as r:
                r.raise_for_status()
                if not r.headers.get("Content-Type").startswith("text/event-stream"):
                    response = await r.json()
                    error_code = response.get("error_code", 0)
                    if error_code != 0:
                        error_msg = response.get("error_msg", "Unknown error")
                        msg = f"call wenxin failed, error_code: {error_code}, error_msg: {error_msg}"
                        raise Exception(msg)
                    yield response

                async def read(content):
                    data = b""
                    async for chunk in content:
                        data += chunk
                        if data.endswith((b"\r\r", b"\n\n", b"\r\n\r\n")):
                            yield data
                            data = b""
                    if data:
                        yield data

                async for line in read(r.content):
                    line_decoded = line.decode("utf-8")
                    if not line_decoded.startswith("data:"):
                        continue
                    event_data = line_decoded[5:].strip()
                    data = json.loads(event_data)
                    yield data


class BaiduCommon(BaseModel):
    client: Any = None  #: :meta private:
    model: str = "ernie-bot"
    """Model name to use. supported models: ernie-bot(wenxin)/ernie-bot-turbo(eb-instant)"""

    temperature: Optional[float] = None
    """A non-negative float that tunes the degree of randomness in generation. Model default is 0.95.
    range: (0.0, 1.0]."""

    penalty_score: Optional[float] = None
    """Repeating punishment involves penalizing already generated tokens to reduce the occurrence of repetition.
    The larger the value, the greater the punishment. Setting it too high can result in poorer text generation
    for long texts. Model default is 1.0.
    range: [1.0, 2.0]."""

    top_p: Optional[float] = None
    """Diversity influences the diversity of output text.
    The larger the value, the stronger the diversity of the generated text. Model default is 0.8.
    range: (0.0, 1.0]."""

    streaming: bool = False
    """Whether to stream the results."""

    request_timeout: Optional[int] = 600
    """Timeout for requests to Baidu Wenxin Completion API. Default is 600 seconds."""

    max_message_length: Optional[int] = 2000
    """Maximum length of last message."""

    baidu_api_key: Optional[str] = None
    """Baidu Cloud API key."""

    baidu_secret_key: Optional[str] = None
    """Baidu Cloud secret key."""

    @root_validator()
    def validate_environment(cls, values: Dict) -> Dict:
        """Validate that api key and python package exists in environment."""
        baidu_api_key = get_from_dict_or_env(
            values, "baidu_api_key", "BAIDU_API_KEY"
        )
        baidu_secret_key = get_from_dict_or_env(
            values, "baidu_secret_key", "BAIDU_SECRET_KEY"
        )
        values["client"] = WenxinClient(
            baidu_api_key=baidu_api_key,
            baidu_secret_key=baidu_secret_key,
            request_timeout=values["request_timeout"],
        )
        return values

    @property
    def _default_params(self) -> Mapping[str, Any]:
        """Get the default parameters for calling Anthropic API."""
        d = {}
        if self.temperature is not None:
            d["temperature"] = self.temperature
        if self.penalty_score is not None:
            d["penalty_score"] = self.penalty_score
        if self.top_p is not None:
            d["top_p"] = self.top_p
        return d

    @property
    def _identifying_params(self) -> Mapping[str, Any]:
        """Get the identifying parameters."""
        return {**{}, **self._default_params}

    def get_num_tokens(self, text: str) -> int:
        """Calculate number of tokens, use text length."""
        return len(text)


class Wenxin(LLM, BaiduCommon):
    r"""Wrapper around Baidu Wenxin large language models.

    To use, you should have the ``requests`` python package installed, and the
    environment variable ``BAIDU_API_KEY`` and ``BAIDU_SECRET_KEY``, or pass
    it as a named parameter to the constructor.

    Example:
        .. code-block:: python
            from langchain_wenxin.llms import Wenxin
            model = Wenxin(model="wenxin", baidu_api_key="my-api-key",
                           baidu_secret_key="my-secret-key")

            # Simplest invocation:
            response = model("What are the biggest risks facing humanity?")
    """

    @root_validator()
    def raise_warning(cls, values: Dict) -> Dict:
        """Raise warning that this class is deprecated."""
        warnings.warn(
            "This Wenxin LLM is deprecated. "
            "Please use `from langchain.chat_models import ChatWenxin` instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return values

    class Config:
        """Configuration for this pydantic object."""

        extra = Extra.forbid

    @property
    def _llm_type(self) -> str:
        """Return type of llm."""
        return "wenxin-llm"

    def _call(
        self,
        prompt: str,
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> str:
        r"""Call out to Baidu Wenxin's completion endpoint.

        Args:
            prompt: The prompt to pass into the model.
            stop: Optional list of stop words to use when generating (not used.).

        Returns:
            The string generated by the model.

        Example:
            .. code-block:: python

                prompt = "What are the biggest risks facing humanity?"
                response = model(prompt)

        """
        params = {**self._default_params, **kwargs}
        if self.streaming:
            stream_resp = self.client.completion_stream(
                model=self.model,
                prompt=prompt,
                history=[],
                **params,
            )
            current_completion = ""
            for data in stream_resp:
                result = data["result"]
                if run_manager:
                    run_manager.on_llm_new_token(result, **data)
                current_completion += result
            return current_completion
        response = self.client.completion(
            model=self.model,
            prompt=prompt,
            history=[],
            **params,
        )
        return response["result"]

    async def _acall(
        self,
        prompt: str,
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> str:
        """Call out to Wenxin's completion endpoint asynchronously."""
        params = {**self._default_params, **kwargs}
        if self.streaming:
            stream_resp = self.client.acompletion_stream(
                model=self.model,
                prompt=prompt,
                history=[],
                **params,
            )
            current_completion = ""
            async for data in stream_resp:
                delta = data["result"]
                current_completion += delta
                if run_manager:
                    await run_manager.on_llm_new_token(delta, **data)
            return current_completion
        response = await self.client.acompletion(
            model=self.model,
            prompt=prompt,
            history=[],
            **params,
        )
        return response["result"]

    def stream(self, prompt: str, stop: Optional[List[str]] = None) -> Generator:
        r"""Call Baidu Wenxin completion_stream and return the resulting generator.

        BETA: this is a beta feature while we figure out the right abstraction.
        Once that happens, this interface could change.

        Args:
            prompt: The prompt to pass into the model.
            stop: Optional list of stop words to use when generating.

        Returns:
            A generator representing the stream of tokens from Baidu Wenxin.

        Example:
            .. code-block:: python


                prompt = "Write a poem about a stream."
                generator = wenxin.stream(prompt)
                for token in generator:
                    yield token
        """
        return self.client.completion_stream(
            model=self.model,
            prompt=prompt,
            history=[],
            **self._default_params)


class ChatWenxin(BaseChatModel, BaiduCommon):
    r"""Wrapper around Baidu Wenxin's large language model.

    To use, you should have the ``requests`` python package installed, and the
    environment variable ``BAIDU_API_KEY`` and ``BAIDU_SECRET_KEY``, or pass
    it as a named parameter to the constructor.

    Example:
        .. code-block:: python
            from langchain_wenxin.llms import ChatWenxin
            model = ChatWenxin(model="wenxin", baidu_api_key="my-api-key",
                           baidu_secret_key="my-secret-key")

            # Simplest invocation:
            response = model("What are the biggest risks facing humanity?")
    """

    class Config:
        """Configuration for this pydantic object."""

        extra = Extra.forbid

    @property
    def _llm_type(self) -> str:
        """Return type of chat model."""
        return "wenxin-chat"

    def _convert_messages_to_prompt(
            self, messages: List[BaseMessage]) -> Tuple[str, List[Tuple[str, str]]]:
        """Format a list of messages into prompt and history.

        Args:
            messages (List[BaseMessage]): List of BaseMessage to combine.

        Returns:
            str: Prompt
            List[Tuple[str, str]]: History
        """
        history = []
        pair = [None, None]
        order_error = "It must be in the order of user, assistant."
        last_message_error = "The last message must be a human message."
        for message in messages[:-1]:
            if message.type == "system":
                history.append((message.content, "OK\n"))
            if pair[0] is None:
                if message.type == "human":
                    pair[0] = message.content
                else:
                    raise ValueError(order_error)
            elif message.type == "ai":
                pair[1] = message.content
                history.append(tuple(pair))
                pair = [None, None]
            else:
                raise ValueError(order_error)
        if not isinstance(messages[-1], HumanMessage):
            raise ValueError(last_message_error)
        return messages[-1].content, history

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
    ) -> ChatResult:
        prompt, history = self._convert_messages_to_prompt(messages)
        params: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "history": history, **self._default_params}

        if self.streaming:
            completion = ""
            stream_resp = self.client.completion_stream(**params)
            for delta in stream_resp:
                result = delta["result"]
                completion += result
                if run_manager:
                    run_manager.on_llm_new_token(
                        result,
                    )
        else:
            response = self.client.completion(**params)
            completion = response
        message = AIMessage(content=completion)
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
    ) -> ChatResult:
        async_not_implemented_error = "Async not implemented for Wenxin Chat Model."
        raise NotImplementedError(async_not_implemented_error)

