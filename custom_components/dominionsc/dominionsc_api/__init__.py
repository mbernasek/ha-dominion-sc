"""Library for getting historical and forecasted usage/cost from dominion energy SC API.

Vendored from dominion-sc-power 0.0.1 (https://github.com/sctigercat1/dominion-sc-power,
Apache-2.0), changed to support logins that hold more than one account.
"""

from .dominionsc import (
    DominionSC,
    DominionSCTFAHandler,
    Forecast,
    UsageRead,
)
from .exceptions import ApiException, CannotConnect, InvalidAuth, MfaChallenge
from .helpers import create_cookie_jar

__all__ = [
    "ApiException",
    "CannotConnect",
    "DominionSC",
    "DominionSCTFAHandler",
    "Forecast",
    "InvalidAuth",
    "MfaChallenge",
    "UsageRead",
    "create_cookie_jar",
]
