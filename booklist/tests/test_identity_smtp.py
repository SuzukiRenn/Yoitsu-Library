"""Runtime mail diagnostics are useful only on the application's network."""
import json
import smtplib
import unittest
from unittest.mock import Mock
from scripts.check_identity_smtp import check


class IdentitySmtpCheckTests(unittest.TestCase):
    env = {'BOOKLIST_QQ_EMAIL_LOGIN': '1', 'BOOKLIST_SMTP_USER': 'private@qq.com',
           'BOOKLIST_SMTP_PASSWORD': 'private-authorization-code'}

    def test_disabled_feature_does_not_connect(self):
        connect = Mock()
        self.assertTrue(check({}, connect)['success'])
        connect.assert_not_called()

    def test_success_authenticates_without_sending_and_closes(self):
        connection = Mock()
        connect = Mock(return_value=connection)
        result = check(self.env, connect)
        self.assertTrue(result['success'])
        self.assertFalse(result['mail_sent'])
        self.assertEqual(connect.call_args.args, ('smtp.qq.com', 465))
        self.assertEqual(connect.call_args.kwargs['timeout'], 10)
        self.assertTrue(connect.call_args.kwargs['context'].check_hostname)
        connection.login.assert_called_once_with('private@qq.com', 'private-authorization-code')
        connection.sendmail.assert_not_called()
        connection.close.assert_called_once()

    def test_network_failure_reports_stage_and_errno_without_exception_text(self):
        result = check(self.env, Mock(side_effect=OSError(101, 'private-authorization-code')))
        self.assertEqual(result, {'success': False, 'stage': 'connect_tls',
            'error_type': 'OSError', 'errno': 101})

    def test_auth_failure_hides_provider_text_and_still_closes(self):
        connection = Mock()
        connection.login.side_effect = smtplib.SMTPAuthenticationError(535, b'private-authorization-code')
        result = check(self.env, Mock(return_value=connection))
        self.assertEqual(result['stage'], 'authentication')
        self.assertEqual(result['smtp_code'], 535)
        self.assertNotIn('private', json.dumps(result))
        connection.close.assert_called_once()

    def test_missing_configuration_fails_before_connection(self):
        connect = Mock()
        result = check({'BOOKLIST_QQ_EMAIL_LOGIN': '1'}, connect)
        self.assertFalse(result['success'])
        self.assertEqual(result['stage'], 'configuration')
        connect.assert_not_called()
