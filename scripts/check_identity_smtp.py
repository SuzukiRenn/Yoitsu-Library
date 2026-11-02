"""Check identity SMTP from its runtime without sending mail or revealing secrets."""
import json
import os
import smtplib
import ssl


def check(environ=None, connect=None):
    environ = os.environ if environ is None else environ
    if environ.get('BOOKLIST_QQ_EMAIL_LOGIN') != '1':
        return {'success': True, 'skipped': 'email_login_disabled'}
    connect = smtplib.SMTP_SSL if connect is None else connect
    connection = None
    stage = 'configuration'
    try:
        user = environ.get('BOOKLIST_SMTP_USER', '')
        password = environ.get('BOOKLIST_SMTP_PASSWORD', '')
        if not user or not password:
            raise ValueError('Missing private configuration')
        stage = 'connect_tls'
        connection = connect(environ.get('BOOKLIST_SMTP_HOST', 'smtp.qq.com'),
            int(environ.get('BOOKLIST_SMTP_PORT', '465')), timeout=10,
            context=ssl.create_default_context())
        stage = 'authentication'
        connection.login(user, password)
        return {'success': True, 'smtp_tls_and_authentication_verified': True,
                'mail_sent': False}
    except Exception as error:
        # Never expose provider text, credentials, recipient addresses or codes.
        result = {'success': False, 'stage': stage, 'error_type': type(error).__name__}
        for field in ('errno', 'smtp_code'):
            value = getattr(error, field, None)
            if type(value) is int:
                result[field] = value
        return result
    finally:
        if connection is not None:
            connection.close()


if __name__ == '__main__':
    result = check()
    print(json.dumps(result), flush=True)
    raise SystemExit(0 if result['success'] else 1)
