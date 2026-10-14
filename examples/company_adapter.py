"""Copy and implement against your company's documented API, not OpenAI assumptions.

Set COMPANY_ADAPTER_FACTORY=examples.company_adapter:build_llm after implementing.
The factory receives server-side credentials. Never log or return them.
"""
from awareflow.domain.errors import ProviderConfigurationError


class CompanyLLM:
    def __init__(self, endpoint, credential, model, timeout, clock):
        self.endpoint, self.credential, self.model = endpoint, credential, model
        self.timeout, self.clock = timeout, clock

    def suggest_queries(self, topic):
        # A company model need not support this helper; deterministic suggestions are fine.
        return [topic + " security awareness", topic + " employee guidance"]

    def generate(self, settings, sources, previous=None, feedback=None):
        # Implement auth headers, request fields, response parsing and errors from company docs.
        # Return ContentBundle.model_validate(mapped_response); never assume an OpenAI wire format.
        raise ProviderConfigurationError("กรุณาเติม company adapter ตามเอกสาร API ของบริษัทก่อนใช้งาน")

    def decide_agent(self, context):
        # Call the company model with AGENT_INSTRUCTIONS, tool contracts and the supplied state.
        # Native tool calling OR structured JSON is acceptable if the MODEL chooses the action.
        # Return {"name": tool_name, "arguments": {...}} matching domain/agent.py.
        # Do not implement a fixed sequence here and claim it is model-directed.
        raise ProviderConfigurationError("กรุณาเติมการเลือกเครื่องมือด้วยโมเดลบริษัทใน decide_agent")


def build_llm(*, endpoint, credential, model, timeout, clock):
    return CompanyLLM(endpoint, credential, model, timeout, clock)
