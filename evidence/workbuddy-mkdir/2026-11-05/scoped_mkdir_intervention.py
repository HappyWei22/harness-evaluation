"""Prepared experiment component. Importing this file does not install hooks.

Only install() changes bindings, in the calling Python process. This is not a
product fix. Both modes observe the original request and response unchanged;
the intervention normalizes one explicitly guarded post-execution exception.
"""
import contextvars
import os
import pathlib
import sitecustomize


class ScopedMkdirIntervention:
    def __init__(self):
        self.active = False
        self.normalize_existing_directory = False
        self.events = []
        self._context = contextvars.ContextVar('mkdir_intervention', default=None)

    def install(self):
        if self.active:
            raise RuntimeError('already installed')
        shim = sitecustomize
        if not shim._broker_host_ops_enabled():
            raise RuntimeError('original host operation broker is not enabled')
        if pathlib.Path.mkdir is not shim._brokered_path_mkdir:
            raise RuntimeError('unexpected existing Path.mkdir binding')
        self.original_mkdir = pathlib.Path.mkdir
        self.original_send = shim._broker_send_request

        def observe_response(request):
            # Always call the original sender, with the original request object.
            response = self.original_send(request)
            context = self._context.get()
            if (context is not None and request.get('operation') == 'mkdir'
                    and request.get('path') == context['target']):
                context['matching_responses'].append({
                    'request_operation': request.get('operation'),
                    'request_path': request.get('path'),
                    'request_recursive': request.get('recursive'),
                    'request_has_exist_ok': 'exist_ok' in request,
                    'response_ok': response.get('ok') if isinstance(response, dict) else None,
                    'response_decision': response.get('decision') if isinstance(response, dict) else None,
                    'response_error': response.get('error') if isinstance(response, dict) else None,
                })
            # No request or response field is rewritten.
            return response

        def scoped_mkdir(path, mode=0o777, parents=False, exist_ok=False):
            context = {'target': os.path.abspath(os.fspath(path)),
                       'intervention_enabled': self.normalize_existing_directory,
                       'matching_responses': [], 'normalized': False}
            token = self._context.set(context)
            try:
                return self.original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)
            except PermissionError:
                responses = context['matching_responses']
                response = responses[0] if len(responses) == 1 else {}
                error = response.get('response_error')
                # Both modes calculate the same eligibility conditions. Only
                # whether the eligible exception returns normally is switched.
                eligible = bool(
                    exist_ok is True and parents is False
                    and response.get('response_ok') is False
                    and response.get('response_decision') == 'host-op'
                    and isinstance(error, str) and error.startswith('EEXIST:')
                    and os.path.isdir(path)
                )
                context['eligible_existing_directory_error'] = eligible
                if eligible and context['intervention_enabled']:
                    context['normalized'] = True
                    return None
                raise
            finally:
                self._context.reset(token)
                self.events.append(context)

        self._observe_response = observe_response
        self._scoped_mkdir = scoped_mkdir
        shim._broker_send_request = observe_response
        pathlib.Path.mkdir = scoped_mkdir
        self.active = True

    def uninstall(self):
        if not self.active:
            raise RuntimeError('not installed')
        if (pathlib.Path.mkdir is not self._scoped_mkdir
                or sitecustomize._broker_send_request is not self._observe_response):
            raise RuntimeError('bindings changed unexpectedly; refusing to overwrite them')
        pathlib.Path.mkdir = self.original_mkdir
        sitecustomize._broker_send_request = self.original_send
        self.active = False
