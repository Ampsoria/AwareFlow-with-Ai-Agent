"""Conservative SMTP outcome handling: a lost acknowledgement is UNKNOWN."""
from email.utils import parseaddr
import smtplib
import ssl

from awareflow.domain.errors import ProviderConfigurationError, Forbidden


def validate_recipient(config, recipient):
    if not config.email_real_send or config.email_transport != "smtp":
        raise Forbidden("ต้องเปิด EMAIL_REAL_SEND_ENABLED และตั้ง EMAIL_TRANSPORT=smtp")
    if not config.smtp_host or not config.smtp_from:
        raise ProviderConfigurationError("ต้องตั้ง SMTP_HOST และ SMTP_FROM")
    if not recipient or "\n" in recipient or "\r" in recipient or parseaddr(recipient)[1] != recipient:
        raise Forbidden("อีเมลผู้รับไม่ถูกต้อง")
    if recipient.lower() not in config.email_allowlist:
        raise Forbidden("ผู้รับไม่อยู่ใน EMAIL_TEST_ALLOWLIST")
    if "\n" in config.smtp_from or "\r" in config.smtp_from or parseaddr(config.smtp_from)[1] != config.smtp_from:
        raise ProviderConfigurationError("SMTP_FROM ต้องเป็นอีเมลเดียว")


class SmtpTransport:
    def __init__(self, config):
        self.config = config

    def send(self, message, recipient):
        validate_recipient(self.config, recipient)
        smtp = None
        sending_data = False
        try:
            context = ssl.create_default_context()
            if self.config.smtp_tls == "ssl":
                smtp = smtplib.SMTP_SSL(self.config.smtp_host, self.config.smtp_port, timeout=30, context=context)
            else:
                smtp = smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=30)
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            if self.config.smtp_username:
                smtp.login(self.config.smtp_username, self.config.smtp_password)
            sending_data = True
            refused = smtp.send_message(message, from_addr=self.config.smtp_from, to_addrs=[recipient])
            return "FAILED" if refused else "ACCEPTED"
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError, smtplib.SMTPAuthenticationError):
            return "FAILED"  # Explicit rejection; server did not accept this message.
        except Exception:
            return "UNKNOWN" if sending_data else "FAILED"
        finally:
            if smtp:
                try:
                    smtp.quit()
                except Exception:
                    pass  # A failed QUIT cannot undo an already acknowledged DATA.
