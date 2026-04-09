"""
Notification service for email alerts.
Sends notifications for trades, opportunities, errors, and daily summaries.
"""
import logging
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, smtp_host: str = "localhost", smtp_port: int = 587,
                 smtp_user: str = "", smtp_password: str = ""):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password

    async def send_email(self, to_email: str, subject: str, body: str):
        """Send an email notification."""
        try:
            message = MIMEMultipart()
            message["From"] = self.smtp_user or "tradium@localhost"
            message["To"] = to_email
            message["Subject"] = f"[Tradium] {subject}"
            message.attach(MIMEText(body, "html"))

            await aiosmtplib.send(
                message,
                hostname=self.smtp_host,
                port=self.smtp_port,
                username=self.smtp_user or None,
                password=self.smtp_password or None,
                use_tls=bool(self.smtp_user),
            )
            logger.info(f"Email sent to {to_email}: {subject}")
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")

    async def notify_trade_executed(self, to_email: str, trade_details: dict):
        subject = f"Trade Executed — {trade_details.get('market_id', 'Unknown')}"
        body = f"""
        <h2>Trade Executed</h2>
        <table>
            <tr><td><b>Market:</b></td><td>{trade_details.get('market_question', trade_details.get('market_id'))}</td></tr>
            <tr><td><b>Strategy:</b></td><td>{trade_details.get('strategy_type')}</td></tr>
            <tr><td><b>Side:</b></td><td>{trade_details.get('side')}</td></tr>
            <tr><td><b>Size:</b></td><td>${trade_details.get('size_usdc', 0):.2f}</td></tr>
            <tr><td><b>Fill Price:</b></td><td>{trade_details.get('fill_price', 'N/A')}</td></tr>
            <tr><td><b>Est. Profit:</b></td><td>${trade_details.get('estimated_profit', 0):.4f}</td></tr>
            <tr><td><b>Paper Trade:</b></td><td>{'Yes' if trade_details.get('is_paper') else 'No'}</td></tr>
        </table>
        """
        await self.send_email(to_email, subject, body)

    async def notify_opportunity_found(self, to_email: str, opp_details: dict):
        subject = f"Opportunity Found — {opp_details.get('strategy_type')} ({opp_details.get('estimated_profit_pct', 0):.2f}%)"
        body = f"""
        <h2>Arbitrage Opportunity Detected</h2>
        <table>
            <tr><td><b>Market:</b></td><td>{opp_details.get('market_question', opp_details.get('market_id'))}</td></tr>
            <tr><td><b>Strategy:</b></td><td>{opp_details.get('strategy_type')}</td></tr>
            <tr><td><b>Prices:</b></td><td>{opp_details.get('outcome_prices')}</td></tr>
            <tr><td><b>Sum:</b></td><td>{opp_details.get('price_sum')}</td></tr>
            <tr><td><b>Est. Profit:</b></td><td>{opp_details.get('estimated_profit_pct', 0):.2f}% (${opp_details.get('estimated_profit_usdc', 0):.2f})</td></tr>
            <tr><td><b>Liquidity:</b></td><td>${opp_details.get('liquidity_depth', 0):.2f}</td></tr>
        </table>
        """
        await self.send_email(to_email, subject, body)

    async def notify_error(self, to_email: str, error_msg: str):
        subject = "Bot Error"
        body = f"<h2>Error Occurred</h2><pre>{error_msg}</pre>"
        await self.send_email(to_email, subject, body)

    async def notify_kill_switch(self, to_email: str):
        subject = "KILL SWITCH ACTIVATED"
        body = """
        <h2 style="color: red;">Kill Switch Activated</h2>
        <p>The trading bot has been stopped. All pending orders have been cancelled.</p>
        <p>Manual intervention is required to restart the bot.</p>
        """
        await self.send_email(to_email, subject, body)

    async def notify_daily_summary(self, to_email: str, summary: dict):
        subject = f"Daily Summary — P&L: ${summary.get('total_pnl', 0):.2f}"
        body = f"""
        <h2>Daily Trading Summary</h2>
        <table>
            <tr><td><b>Total Trades:</b></td><td>{summary.get('total_trades', 0)}</td></tr>
            <tr><td><b>Total P&L:</b></td><td>${summary.get('total_pnl', 0):.2f}</td></tr>
            <tr><td><b>Win Rate:</b></td><td>{summary.get('win_rate', 0)*100:.1f}%</td></tr>
            <tr><td><b>Fees Paid:</b></td><td>${summary.get('total_fees', 0):.2f}</td></tr>
            <tr><td><b>Volume:</b></td><td>${summary.get('total_volume', 0):.2f}</td></tr>
        </table>
        """
        await self.send_email(to_email, subject, body)
