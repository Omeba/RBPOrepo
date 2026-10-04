from django.contrib.auth import logout


class CredentialVersionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            if request.session.get('credential_version') != request.user.credential_version:
                logout(request)
        response = self.get_response(request)
        # Forms and authenticated pages must not persist credentials in browser caches.
        response['Cache-Control'] = 'no-store'
        return response
