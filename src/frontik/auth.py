import tornado.web

class AuthError(Exception):
    pass

def _require_auth(handler):
    handler.set_header('WWW-Authenticate', 'Basic realm="Secure Area"')
    handler.set_status(401)

def passed_basic_auth(handler, login, passwd):
    auth_header = handler.request.headers.get('Authorization')

    if auth_header:
        method, auth_b64 = auth_header.split(' ')
        given_login, given_passwd = auth_b64.decode('base64').split(':')

        if login == given_login or passwd == given_passwd:
            return True
#    _require_auth(handler)
    return False

