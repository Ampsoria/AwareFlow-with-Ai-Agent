"""REST adapters for local servers and non-OpenAI APIs. No automatic provider fallback."""
import json

import httpx

from awareflow.domain.agent import AGENT_INSTRUCTIONS, AgentAction, TOOL_SPECS, function_tools
from awareflow.domain.errors import ProviderUnavailable, TransientProviderError
from .llm_base import StructuredLLM


DEFAULT_ENDPOINTS = {
    'ollama': 'http://127.0.0.1:11434',
    'anthropic': 'https://api.anthropic.com/v1',
    'gemini': 'https://generativelanguage.googleapis.com/v1beta',
}


def strict_json_schema(schema):
    """Close every object for OpenAI-compatible strict schema mode."""
    if isinstance(schema, list):
        return [strict_json_schema(v) for v in schema]
    if not isinstance(schema, dict):
        return schema
    result = {k: strict_json_schema(v) for k, v in schema.items() if k != 'default'}
    if result.get('type') == 'object':
        result['additionalProperties'] = False
        result['required'] = list(result.get('properties', {}))
    return result


class HttpStructuredLLM(StructuredLLM):
    def __init__(self, config, clock, client=None):
        self.config, self.clock, self.model = config, clock, config.llm_model
        self.search_provider = config.search_provider
        self.endpoint = (config.llm_endpoint or DEFAULT_ENDPOINTS.get(self.provider_name, '')).rstrip('/')
        self.client = client or httpx.Client(timeout=config.provider_timeout, follow_redirects=False)

    def _post(self, path, body, headers):
        try:
            response = self.client.post(self.endpoint + path, json=body, headers=headers, follow_redirects=False)
        except httpx.RequestError:
            raise TransientProviderError('เชื่อมต่อบริการโมเดลไม่ได้ ตรวจเซิร์ฟเวอร์ เครือข่าย และ timeout') from None
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientProviderError('บริการโมเดลไม่พร้อมชั่วคราว')
        if not 200 <= response.status_code < 300:
            # Never include response bodies, URLs or request headers in user-facing errors/logs.
            raise ProviderUnavailable('เรียกโมเดลไม่สำเร็จ (HTTP ' + str(response.status_code) + ') ตรวจ provider/endpoint/model/key/output mode')
        try:
            result = response.json()
            if not isinstance(result, dict) or result.get('error'):
                raise ValueError()
            return result
        except ValueError:
            raise ProviderUnavailable('บริการโมเดลคืน response ที่อ่านไม่ได้') from None

    def _bearer(self):
        return {'Authorization': 'Bearer ' + self.config.llm_key} if self.config.llm_key else {}

    @staticmethod
    def _messages(schema, system, data):
        instructions = system + '\nReturn ONLY one JSON object matching this schema, without markdown or explanations:\n' + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        return [{'role': 'system', 'content': instructions},
                {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}]

    @staticmethod
    def _decode(schema, text):
        if not isinstance(text, str) or not text.strip():
            raise ProviderUnavailable('โมเดลไม่คืนข้อความ JSON ที่ใช้งานได้')
        text = text.strip()
        # Some text-only servers wrap JSON in a single code fence. Never extract/execute arbitrary prose.
        if text.startswith(('```json\n', '```\n')) and text.endswith('```'):
            text = text.split('\n', 1)[1][:-3].strip()
        return schema.model_validate_json(text)

    @staticmethod
    def _envelope_error():
        return ProviderUnavailable('โมเดลไม่คืนผลสมบูรณ์ตามรูปแบบ API ที่เลือก (อาจถูกตัดหรือปฏิเสธ)')


class OpenAICompatibleLLM(HttpStructuredLLM):
    provider_name = 'openai_compatible'

    def _parse(self, schema, system, data):
        body = {'model': self.model, 'messages': self._messages(schema, system, data),
                'stream': False, 'max_tokens': self.config.llm_max_output_tokens}
        if self.config.llm_output_mode == 'json_schema':
            body['response_format'] = {'type': 'json_schema', 'json_schema': {
                'name': schema.__name__, 'strict': True, 'schema': strict_json_schema(schema.model_json_schema())}}
        elif self.config.llm_output_mode == 'json':
            body['response_format'] = {'type': 'json_object'}
        result = self._post('/chat/completions', body, self._bearer())
        try:
            choices = result['choices']
            if len(choices) != 1 or choices[0]['finish_reason'] != 'stop':
                raise ValueError()
            message = choices[0]['message']
            if message.get('refusal') or message.get('tool_calls'):
                raise ValueError()
            content = message['content']
        except (KeyError, IndexError, TypeError, ValueError):
            raise self._envelope_error() from None
        return self._decode(schema, content)


class OllamaLLM(HttpStructuredLLM):
    provider_name = 'ollama'

    def _parse(self, schema, system, data):
        body = {'model': self.model, 'messages': self._messages(schema, system, data), 'stream': False,
                'options': {'num_predict': self.config.llm_max_output_tokens}}
        if self.config.llm_output_mode == 'json_schema':
            body['format'] = schema.model_json_schema()
        elif self.config.llm_output_mode == 'json':
            body['format'] = 'json'
        result = self._post('/api/chat', body, self._bearer())
        try:
            if result['done'] is not True or result.get('done_reason') not in (None, 'stop'):
                raise ValueError()
            message = result['message']
            if message.get('tool_calls'):
                raise ValueError()
            content = message['content']
        except (KeyError, TypeError, ValueError):
            raise self._envelope_error() from None
        return self._decode(schema, content)


class AnthropicLLM(HttpStructuredLLM):
    provider_name = 'anthropic'

    def _request(self, system, data, tools, choice):
        return self._post('/messages', {
            'model': self.model, 'max_tokens': self.config.llm_max_output_tokens, 'stream': False,
            'system': system, 'messages': [{'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}],
            'tools': tools, 'tool_choice': choice,
        }, {'x-api-key': self.config.llm_key, 'anthropic-version': '2023-06-01'})

    def _tool(self, result):
        try:
            calls = [item for item in result['content'] if item['type'] == 'tool_use']
            if result['stop_reason'] != 'tool_use' or len(calls) != 1:
                raise ValueError()
            return calls[0]['name'], calls[0]['input']
        except (KeyError, TypeError, ValueError):
            raise self._envelope_error() from None

    def _parse(self, schema, system, data):
        tools = [{'name': 'return_result', 'description': 'Return the requested structured result.', 'input_schema': schema.model_json_schema()}]
        name, result = self._tool(self._request(system, data, tools, {'type': 'tool', 'name': 'return_result'}))
        if name != 'return_result':
            raise self._envelope_error()
        return schema.model_validate(result)

    def decide_agent(self, context):
        tools = [{'name': t['name'], 'description': t['description'], 'input_schema': t['parameters']} for t in function_tools()]
        name, arguments = self._tool(self._request(AGENT_INSTRUCTIONS, context, tools, {'type': 'any'}))
        action = AgentAction(name=name, arguments=arguments)
        TOOL_SPECS[action.name][0].model_validate(action.arguments)
        return action.model_dump()


class GeminiLLM(HttpStructuredLLM):
    provider_name = 'gemini'

    def _parse(self, schema, system, data):
        messages = self._messages(schema, system, data)
        generation = {'maxOutputTokens': self.config.llm_max_output_tokens}
        if self.config.llm_output_mode != 'text':
            output = {'mimeType': 'application/json'}
            if self.config.llm_output_mode == 'json_schema':
                output['schema'] = schema.model_json_schema()
            generation['responseFormat'] = {'text': output}
        result = self._post('/models/' + self.model + ':generateContent', {
            'systemInstruction': {'parts': [{'text': messages[0]['content']}]},
            'contents': [{'role': 'user', 'parts': [{'text': messages[1]['content']}]}],
            'generationConfig': generation,
        }, {'x-goog-api-key': self.config.llm_key})
        try:
            candidates = result['candidates']
            if len(candidates) != 1 or candidates[0]['finishReason'] != 'STOP':
                raise ValueError()
            parts = candidates[0]['content']['parts']
            if any('functionCall' in p for p in parts):
                raise ValueError()
            content = ''.join(p['text'] for p in parts if 'text' in p and not p.get('thought'))
        except (KeyError, TypeError, ValueError):
            raise self._envelope_error() from None
        return self._decode(schema, content)
