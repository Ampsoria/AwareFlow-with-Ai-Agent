"""Explicit connection check. Sends synthetic prompts, never executes selected tools."""
from awareflow.domain.agent import AgentAction, TOOL_SPECS
from awareflow.infrastructure.llm_base import QuerySuggestions
from awareflow.infrastructure.providers import create_llm


def check_llm(config, clock, llm=None):
    provider = llm or create_llm(config, clock)
    QuerySuggestions(queries=provider.suggest_queries('Phishing awareness / connection test'))
    action = AgentAction.model_validate(provider.decide_agent({
        'goal': 'Prepare a phishing awareness lesson. This is a connection test; select the next tool only.',
        'settings': {'topic': 'Phishing', 'queries': ['phishing awareness'], 'content_mode': 'evergreen'},
        'human_feedback': None, 'previous_version': None, 'evidence': [], 'draft': None,
        'checked': False, 'observations': [], 'remaining': {'steps': 4, 'searches': 1, 'drafts': 1},
    }))
    TOOL_SPECS[action.name][0].model_validate(action.arguments)
    return {'provider': config.llm_provider, 'model': 'mock' if config.llm_provider == 'mock' else config.llm_model, 'status': 'OK',
            'checks': ['structured_response', 'agent_decision'], 'executed_tools': 0,
            'note': 'Synthetic prompts only; this does not validate full lesson quality, search or SMTP.'}
