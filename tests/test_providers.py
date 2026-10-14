"""REST protocol/agent contracts tested with HTTPX MockTransport; no live network calls."""
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError as SchemaError

from awareflow.application.agent import AwarenessAgent
from awareflow.application.diagnostics import check_llm
from awareflow.bootstrap import FixedClock
from awareflow.config import Config
from awareflow.demo import START, settings
from awareflow.domain.errors import ProviderConfigurationError, ProviderUnavailable, TransientProviderError
from awareflow.infrastructure.http_llm import OpenAICompatibleLLM, OllamaLLM, AnthropicLLM, GeminiLLM
from awareflow.infrastructure.llm_base import QuerySuggestions
from awareflow.infrastructure.mock import MockLLM, MockSearch
from awareflow.infrastructure.providers import create_llm
from test_core import FastPdf


ADAPTERS = {'openai_compatible': OpenAICompatibleLLM, 'ollama': OllamaLLM, 'anthropic': AnthropicLLM, 'gemini': GeminiLLM}


def envelope(provider, value, tool='return_result'):
    text = json.dumps(value, ensure_ascii=False)
    if provider == 'openai_compatible':
        return {'choices': [{'finish_reason': 'stop', 'message': {'role':'assistant', 'content':text}}]}
    if provider == 'ollama':
        return {'done': True, 'done_reason':'stop', 'message': {'role':'assistant', 'content':text}}
    if provider == 'anthropic':
        return {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'id':'test-call', 'name':tool, 'input':value}]}
    return {'candidates': [{'finishReason': 'STOP', 'content': {'parts':[{'text':text}]}}]}


def json_decision(name, **kwargs):
    return dict(name=name, query=kwargs.get('query',''), source_ids=kwargs.get('source_ids',[]),
                instructions=kwargs.get('instructions',''), message=kwargs.get('message',''))


class ProviderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.config = Config(root, root/'test.sqlite3', llm_model='test-model', signing_secret='x'*48)
        self.clock = FixedClock(START)

    def provider(self, name, handler, **changes):
        endpoint = {'openai_compatible':'http://127.0.0.1:1234/v1', 'ollama':'http://127.0.0.1:11434',
                    'anthropic':'https://api.anthropic.com/v1', 'gemini':'https://generativelanguage.googleapis.com/v1beta'}[name]
        config = replace(self.config, llm_provider=name, llm_endpoint=endpoint, llm_key='test-key', **changes)
        config.validate()
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return ADAPTERS[name](config, self.clock, client)

    def test_provider_wire_shapes_and_credentials(self):
        for name in ADAPTERS:
            with self.subTest(provider=name):
                seen = []
                def handler(request):
                    seen.append(request)
                    return httpx.Response(200, json=envelope(name, {'queries':['phishing employee awareness']}))
                provider = self.provider(name, handler, llm_output_mode='json_schema')
                self.assertEqual(provider.suggest_queries('Phishing'), ['phishing employee awareness'])
                req, body = seen[0], json.loads(seen[0].content)
                self.assertNotIn('test-key', str(req.url))
                self.assertNotIn('test-key', str(body))
                if name == 'openai_compatible':
                    self.assertEqual(req.url.path, '/v1/chat/completions')
                    self.assertEqual(body['response_format']['type'],'json_schema')
                    self.assertEqual(req.headers['authorization'],'Bearer test-key')
                    self.assertFalse(body['stream'])
                elif name == 'ollama':
                    self.assertEqual(req.url.path, '/api/chat')
                    self.assertEqual(body['format']['type'],'object')
                    self.assertFalse(body['stream'])
                    self.assertEqual(body['options']['num_predict'],6000)
                elif name == 'anthropic':
                    self.assertEqual(req.url.path, '/v1/messages')
                    self.assertEqual(req.headers['x-api-key'],'test-key')
                    self.assertEqual(req.headers['anthropic-version'],'2023-06-01')
                    self.assertEqual(body['tool_choice'], {'type':'tool','name':'return_result'})
                else:
                    self.assertEqual(req.url.path, '/v1beta/models/test-model:generateContent')
                    self.assertEqual(req.headers['x-goog-api-key'],'test-key')
                    self.assertEqual(body['generationConfig']['responseFormat']['text']['schema']['type'],'object')
                    self.assertNotIn('Authorization',req.headers)

    def test_json_model_decisions_are_mapped_to_real_tool_contract(self):
        for name in ('openai_compatible','ollama','gemini'):
            with self.subTest(provider=name):
                seen = []
                def handler(request):
                    seen.append(json.loads(request.content))
                    return httpx.Response(200, json=envelope(name, json_decision('search_sources', query='new query chosen by model')))
                provider = self.provider(name,handler)
                result = provider.decide_agent({'goal':'find guidance','observations':[{'result':{'accepted':[]}}]})
                self.assertEqual(result, {'name':'search_sources','arguments':{'query':'new query chosen by model'}})
                self.assertIn('find guidance',str(seen))
                self.assertIn('submit_for_review',str(seen))

    def test_anthropic_uses_native_agent_tools(self):
        seen=[]
        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200,json=envelope('anthropic',{'query':'chosen query'},tool='search_sources'))
        model=self.provider('anthropic',handler)
        self.assertEqual(model.decide_agent({'goal':'test'}),{'name':'search_sources','arguments':{'query':'chosen query'}})
        self.assertEqual(seen[0]['tool_choice'],{'type':'any'})
        self.assertEqual(len(seen[0]['tools']),5)
        self.assertNotIn('send_mail',str(seen[0]['tools']))

    def test_whole_agent_runs_on_gemini_protocol_with_mocked_responses(self):
        sources = MockSearch(self.clock).search(settings())
        bundle = MockLLM(self.clock).generate(settings(),sources).model_dump(mode='json')
        outputs = [json_decision('search_sources',query='phishing for employees'),
                   json_decision('compose_lesson',source_ids=['DEMO-1'],instructions='write a short lesson'),
                   bundle, json_decision('check_lesson'), json_decision('submit_for_review')]
        requests=[]
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200,json=envelope('gemini',outputs.pop(0)))
        llm=self.provider('gemini',handler)
        events=[]
        result=AwarenessAgent(llm,MockSearch(self.clock),FastPdf()).run(settings(),
            before_step=lambda step,tool,args: events.append(tool), after_step=lambda *args: None, guard=lambda: None)
        self.assertEqual(events,['search_sources','compose_lesson','check_lesson','submit_for_review'])
        self.assertEqual(result.bundle.provenance.llm_provider,'gemini')
        self.assertTrue(result.bundle.provenance.is_demo)
        self.assertEqual(len(requests),5)
        # A later model decision receives the actual prior tool observation and draft.
        context=json.loads(requests[-1]['contents'][0]['parts'][0]['text'])['state']
        self.assertTrue(context['checked'])
        self.assertEqual(context['observations'][-1]['result']['code'],'CHECK_PASSED')

    def test_generation_stamps_actual_provider_instead_of_model_claim(self):
        sources=MockSearch(self.clock).search(settings())
        bundle=MockLLM(self.clock).generate(settings(),sources).model_dump(mode='json')
        for name in ADAPTERS:
            with self.subTest(provider=name):
                provider=self.provider(name,lambda _:httpx.Response(200,json=envelope(name,bundle)))
                result=provider.generate(settings(),sources)
                self.assertEqual(result.provenance.llm_provider,name)
                self.assertEqual(result.provenance.model,'test-model')
                self.assertTrue(result.provenance.is_demo)
                self.assertEqual(result.provenance.search_provider,'mock')

    def test_modes_are_explicit_without_silent_provider_or_protocol_fallback(self):
        for name in ('openai_compatible','ollama','gemini'):
            for mode in ('json_schema','json','text'):
                with self.subTest(provider=name,mode=mode):
                    calls=[]
                    def handler(request):
                        calls.append(json.loads(request.content))
                        return httpx.Response(200,json=envelope(name,{'queries':['a']}))
                    self.provider(name,handler,llm_output_mode=mode).suggest_queries('topic')
                    self.assertEqual(len(calls),1)
                    body=calls[0]
                    if name=='openai_compatible':
                        self.assertEqual('response_format' in body,mode!='text')
                    elif name=='ollama':
                        self.assertEqual('format' in body,mode!='text')
                    else:
                        self.assertEqual('responseFormat' in body['generationConfig'],mode!='text')

    def test_error_body_and_key_never_escape_and_transient_status_is_classified(self):
        for status in (401,400,429,503):
            provider=self.provider('gemini',lambda _:httpx.Response(status,json={'error':'private-secret-key'}))
            expected=TransientProviderError if status in (429,503) else ProviderUnavailable
            with self.assertRaises(expected) as caught:
                provider.suggest_queries('test')
            self.assertNotIn('private-secret',str(caught.exception))
            self.assertNotIn('test-key',str(caught.exception))

    def test_redirect_is_not_followed_with_credentials(self):
        calls=[]
        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(307,headers={'location':'https://outside.invalid/collect'})
        provider=self.provider('gemini',handler)
        with self.assertRaises(ProviderUnavailable): provider.suggest_queries('test')
        self.assertEqual(len(calls),1)
        self.assertNotIn('outside.invalid',calls[0])

    def test_malformed_and_truncated_responses_cannot_be_used(self):
        bad={
            'openai_compatible':{'choices':[{'finish_reason':'length','message':{'content':'{"queries":["a"]}'}}]},
            'ollama':{'done':True,'done_reason':'length','message':{'content':'{"queries":["a"]}'}},
            'anthropic':{'stop_reason':'max_tokens','content':[]},
            'gemini':{'candidates':[{'finishReason':'MAX_TOKENS','content':{'parts':[{'text':'{"queries":["a"]}'}]}}]},
        }
        for name in ADAPTERS:
            for payload in (bad[name],{}, {'error':'secret'}):
                with self.subTest(provider=name,payload=payload):
                    provider=self.provider(name,lambda _:httpx.Response(200,json=payload))
                    with self.assertRaises(ProviderUnavailable): provider.suggest_queries('test')

    def test_bad_json_and_forbidden_decision_do_not_execute(self):
        for response in ({'queries':[]},json_decision('send_mail')):
            provider=self.provider('gemini',lambda _:httpx.Response(200,json=envelope('gemini',response)))
            with self.assertRaises(SchemaError):
                if 'queries' in response: provider.suggest_queries('test')
                else: provider.decide_agent({'goal':'test'})

    def test_local_model_does_not_require_or_receive_unrelated_key(self):
        seen=[]
        def handler(request):
            seen.append(request)
            return httpx.Response(200,json=envelope('ollama',{'queries':['a']}))
        config=replace(self.config,llm_provider='ollama',llm_key='')
        config.validate()
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            OllamaLLM(config,self.clock,client).suggest_queries('test')
        self.assertNotIn('authorization',seen[0].headers)
        self.assertEqual(seen[0].url.host,'127.0.0.1')

    def test_endpoint_rules_allow_local_and_explicit_lan_only(self):
        for endpoint in ('http://localhost:11434','http://127.0.0.1:1234/v1','http://[::1]:11434','https://model.example.invalid/v1'):
            replace(self.config,llm_provider='ollama',llm_endpoint=endpoint).validate()
        for endpoint in ('http://192.168.1.20:11434','http://model.internal:8000/v1'):
            with self.assertRaises(ProviderConfigurationError): replace(self.config,llm_endpoint=endpoint).validate()
            replace(self.config,llm_endpoint=endpoint,llm_allow_http=True).validate()
        for endpoint in ('https://user:key@host.invalid','https://host.invalid?key=secret','http://localhost.evil.invalid:11434',
                         'http://localhost:bad','file:///tmp/model','https://host.invalid/#secret'):
            with self.assertRaises(ProviderConfigurationError): replace(self.config,llm_endpoint=endpoint).validate()

    def test_provider_config_requirements_and_registry(self):
        for name,kind in ADAPTERS.items():
            endpoint='http://localhost:8000/v1' if name=='openai_compatible' else ''
            config=replace(self.config,llm_provider=name,llm_endpoint=endpoint,llm_key='test')
            config.validate()
            provider=create_llm(config,self.clock)
            self.addCleanup(provider.client.close)
            self.assertIsInstance(provider,kind)
        for config in (replace(self.config,llm_provider='gemini'),
                       replace(self.config,llm_provider='gemini',llm_key='test',llm_model='../other'),
                       replace(self.config,llm_provider='openai_compatible'),
                       replace(self.config,llm_provider='ollama',llm_model=''),
                       replace(self.config,llm_output_mode='unknown')):
            with self.assertRaises(ProviderConfigurationError): config.validate()

    def test_environment_selects_only_matching_key_and_hides_secrets(self):
        for name,keyvar in [('gemini','GEMINI_API_KEY'),('anthropic','ANTHROPIC_API_KEY'),('openai','OPENAI_API_KEY')]:
            with self.subTest(provider=name),patch.dict(os.environ,{'LLM_PROVIDER':name,'LLM_MODEL':'test-model',
                    keyvar:'right-private-key','LLM_API_KEY':''},clear=True),patch('dotenv.load_dotenv'):
                config=Config.from_env()
                self.assertEqual(config.llm_key,'right-private-key')
                self.assertNotIn('right-private-key',repr(config))
        with patch.dict(os.environ,{'LLM_PROVIDER':'ollama','LLM_MODEL':'test','OPENAI_API_KEY':'unrelated-key'},clear=True),patch('dotenv.load_dotenv'):
            self.assertEqual(Config.from_env().llm_key,'')
        with patch.dict(os.environ,{'LLM_PROVIDER':'gemini','LLM_MODEL':'test','GEMINI_API_KEY':'old','LLM_API_KEY':'explicit'},clear=True),patch('dotenv.load_dotenv'):
            self.assertEqual(Config.from_env().llm_key,'explicit')

    def test_connection_check_uses_two_synthetic_requests_without_executing_tools(self):
        outputs=[{'queries':['phishing awareness']},json_decision('search_sources',query='test query')]
        provider=self.provider('gemini',lambda _:httpx.Response(200,json=envelope('gemini',outputs.pop(0))))
        result=check_llm(provider.config,self.clock,provider)
        self.assertEqual(result['status'],'OK')
        self.assertEqual(result['executed_tools'],0)
        self.assertEqual(outputs,[])
        self.assertFalse(self.config.database_path.exists())


if __name__=='__main__': unittest.main()
