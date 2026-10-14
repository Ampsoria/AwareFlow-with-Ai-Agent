"""Bounded, model-directed tool loop. The model chooses actions; Python enforces authority."""
from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import ValidationError as SchemaError

from awareflow.domain.agent import AgentAction, TOOL_SPECS
from awareflow.domain.errors import AgentNeedsInput, ProviderConfigurationError, ValidationError
from awareflow.domain.models import ContentBundle, RoundSettings


@dataclass(frozen=True)
class AgentResult:
    bundle: ContentBundle
    sources: list
    pdf: bytes


class AwarenessAgent:
    def __init__(self, llm, search, pdf, max_steps=12, max_searches=5, max_drafts=3):
        self.llm, self.search, self.pdf = llm, search, pdf
        self.max_steps, self.max_searches, self.max_drafts = max_steps, max_searches, max_drafts

    @staticmethod
    def _eligibility(source, settings):
        if source.provenance == 'mock':
            return None
        host = (urlsplit(source.url or '').hostname or '').lower().rstrip('.')
        if not any(host == d.hostname or (d.include_subdomains and host.endswith('.' + d.hostname)) for d in settings.allowed_domains):
            return 'DOMAIN_NOT_ALLOWED'
        actual = source.event_at or source.published_at
        if settings.content_mode == 'recent':
            if not actual:
                return 'NO_VERIFIED_PUBLICATION_OR_EVENT_DATE'
            if not str(settings.search_window.start_date) <= actual[:10] <= str(settings.search_window.end_date):
                return 'OUTSIDE_DATE_WINDOW'
        return None

    def run(self, settings, previous=None, feedback=None, *, before_step, after_step, guard):
        if not callable(getattr(self.llm, 'decide_agent', None)):
            raise ProviderConfigurationError('โมเดลต้องรองรับ decide_agent(context) สำหรับเลือกเครื่องมือ')
        pool, observations, searched = {}, [], set()
        draft, selected, pdf_bytes = None, [], None
        searches, drafts = 0, 0
        for step in range(1, self.max_steps + 1):
            guard()  # Stop stale work before each model request and before each tool invocation.
            context = {
                'goal': settings.agent_goal or ('เตรียมบทเรียน Security Awareness เรื่อง ' + settings.topic),
                'settings': settings.model_dump(mode='json'), 'human_feedback': feedback,
                'previous_version': previous.model_dump(mode='json') if previous else None,
                'evidence': [s.model_dump(mode='json') for s in pool.values()],
                'draft': draft.model_dump(mode='json') if draft else None,
                'checked': pdf_bytes is not None, 'observations': observations,
                'remaining': {'steps': self.max_steps - step + 1, 'searches': self.max_searches - searches,
                              'drafts': self.max_drafts - drafts},
            }
            action = None
            try:
                decision = self.llm.decide_agent(context)
                action = AgentAction.model_validate(decision)
                args = TOOL_SPECS[action.name][0].model_validate(action.arguments)
            except SchemaError:
                # Do not persist raw invalid payloads or provider error messages.
                before_step(step, 'invalid_action', {})
                result = {'ok': False, 'code': 'INVALID_TOOL_CALL', 'message': 'Choose one allowed tool with its exact arguments.'}
                after_step(step, 'REJECTED', result)
                observations.append({'tool': 'invalid_action', 'result': result})
                continue
            guard()
            before_step(step, action.name, args.model_dump(mode='json'))
            result, outcome = {}, None
            try:
                if action.name == 'search_sources':
                    query_key = ' '.join(args.query.casefold().split())
                    if searches >= self.max_searches:
                        result = {'ok': False, 'code': 'SEARCH_BUDGET_EXHAUSTED'}
                    elif query_key in searched:
                        result = {'ok': False, 'code': 'DUPLICATE_QUERY', 'message': 'Use a different query or the current evidence.'}
                    else:
                        searches += 1
                        searched.add(query_key)
                        search_settings = RoundSettings.model_validate({**settings.model_dump(), 'queries': [args.query]})
                        found = self.search.search(search_settings)
                        guard()
                        accepted, rejected = [], []
                        for source in found[:3]:
                            reason = self._eligibility(source, settings)
                            if reason:
                                rejected.append({'source_id': source.source_id, 'code': reason})
                            else:
                                # Keep existing evidence immutable, including sources used by a checked draft.
                                pool.setdefault(source.source_id, source)
                                accepted.append(source.source_id)
                        result = {'ok': True, 'accepted': accepted, 'rejected': rejected,
                                  'message': 'Assess relevance and coverage; search again if needed.'}
                elif action.name == 'compose_lesson':
                    if drafts >= self.max_drafts:
                        result = {'ok': False, 'code': 'DRAFT_BUDGET_EXHAUSTED'}
                    elif len(set(args.source_ids)) != len(args.source_ids) or not all(i in pool for i in args.source_ids):
                        result = {'ok': False, 'code': 'UNKNOWN_OR_DUPLICATE_SOURCE', 'message': 'Use IDs from accepted evidence only.'}
                    else:
                        drafts += 1
                        prior_draft = draft or previous
                        draft, pdf_bytes = None, None
                        selected = [pool[i] for i in args.source_ids]
                        instructions = '\n'.join(x for x in (feedback, 'Agent writing instructions: ' + args.instructions) if x)
                        draft = ContentBundle.model_validate(self.llm.generate(settings, selected, prior_draft, instructions))
                        guard()
                        result = {'ok': True, 'code': 'DRAFT_CREATED', 'draft_number': drafts,
                                  'message': 'Inspect the current draft against evidence and goal, then check_lesson.'}
                elif action.name == 'check_lesson':
                    pdf_bytes = None
                    if draft is None:
                        result = {'ok': False, 'code': 'NO_DRAFT'}
                    else:
                        draft.validate_against(settings, selected)
                        if previous is not None and not draft.change_summary:
                            raise ValidationError('ฉบับแก้ไขต้องมีสรุปสิ่งที่แก้')
                        pdf_bytes = self.pdf.lesson(draft, selected)
                        guard()
                        result = {'ok': True, 'code': 'CHECK_PASSED', 'message': 'Structure, references and PDF passed. Assess factual relevance yourself before requesting human review.'}
                elif action.name == 'submit_for_review':
                    if draft is None or pdf_bytes is None:
                        result = {'ok': False, 'code': 'UNCHECKED_DRAFT', 'message': 'Check the latest draft successfully first.'}
                    else:
                        draft.validate_against(settings, selected)
                        result = {'ok': True, 'code': 'READY_FOR_HUMAN_REVIEW'}
                        outcome = AgentResult(draft, selected, pdf_bytes)
                elif action.name == 'request_human_input':
                    result = {'ok': True, 'code': 'HUMAN_INPUT_REQUIRED', 'message': args.message}
                    outcome = AgentNeedsInput('HUMAN_INPUT_REQUIRED')
            except SchemaError as exc:
                # Pydantic error messages/context may contain raw model text; only fixed type codes leave here.
                result = {'ok': False, 'code': 'INVALID_CONTENT_SCHEMA',
                          'errors': sorted({e['type'] for e in exc.errors(include_input=False)})[:8],
                          'message': 'Regenerate a schema-valid lesson and quiz.'}
            except ValidationError as exc:
                result = {'ok': False, 'code': 'CONTENT_CHECK_FAILED', 'message': str(exc)}
            except Exception:
                after_step(step, 'FAILED', {'ok': False, 'code': 'TOOL_UNAVAILABLE'})
                raise
            after_step(step, 'SUCCEEDED' if result.get('ok') else 'REJECTED', result)
            observations.append({'tool': action.name, 'arguments': args.model_dump(mode='json'), 'result': result})
            if isinstance(outcome, AgentNeedsInput):
                raise outcome
            if outcome is not None:
                return outcome
        before_step(self.max_steps + 1, 'budget_exhausted', {})
        after_step(self.max_steps + 1, 'REJECTED', {'ok': False, 'code': 'AGENT_BUDGET_EXHAUSTED'})
        raise AgentNeedsInput('AGENT_BUDGET_EXHAUSTED')
