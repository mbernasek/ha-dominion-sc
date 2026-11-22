"""Tests for choosing an account when a login holds several open accounts."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dominionsc.config_flow import (
    CONF_TFA_CODE,
    CONF_TFA_METHOD,
    _validate_login,
)
from custom_components.dominionsc.const import (
    CONF_ACCOUNT,
    CONF_COST_MODE,
    CONF_EXTENDED_BACKFILL,
    CONF_EXTENDED_COST_BACKFILL,
    CONF_LOGIN_DATA,
    COST_MODE_RATE_8,
    DOMAIN,
)
from custom_components.dominionsc.coordinator import DominionSCCoordinator
from custom_components.dominionsc.dominionsc_api import (
    AccountChoice,
    AccountSelectionRequired,
    ApiException,
    CannotConnect,
    InvalidAuth,
    MfaChallenge,
)

VALIDATE_LOGIN = "custom_components.dominionsc.config_flow._validate_login"

# Every value below is made up.
FIRST = AccountChoice("7-4410", "1 MAIN ST APT 110")
SECOND = AccountChoice("8-2071", "2 OAK ST")
THIRD = AccountChoice("5-6638", "3 PINE RD")


def _choice_needed(*accounts: AccountChoice) -> AccountSelectionRequired:
    return AccountSelectionRequired("Several accounts are open", list(accounts))


def _login_data(result: dict) -> dict:
    """Return the data the most recent login check was given."""
    return dict(result.call_args.args[1])


async def _user_flow_to_tfa_code(hass: HomeAssistant, user_input: dict) -> dict:
    """Start setup and stop at the form that asks for the security code."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["step_id"] == "tfa_options"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TFA_METHOD: "sms"}
    )
    assert result["step_id"] == "tfa_code"
    return result


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


async def test_setup_asks_which_account_and_stores_the_answer(
    hass: HomeAssistant,
    mock_tfa_handler: AsyncMock,
    mock_setup_entry: AsyncMock,
    user_input: dict,
) -> None:
    """Several open accounts lead to a picker, and the pick lands in the entry."""
    with patch(
        VALIDATE_LOGIN,
        side_effect=[
            MfaChallenge("TFA Required", mock_tfa_handler),
            _choice_needed(FIRST, SECOND),
            None,
        ],
    ) as validate:
        result = await _user_flow_to_tfa_code(hass, user_input)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TFA_CODE: "123456"}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "select_account"
        assert result["data_schema"].schema[CONF_ACCOUNT].container == {
            "0": "1 MAIN ST APT 110 (account ending 7-4410)",
            "1": "2 OAK ST (account ending 8-2071)",
        }
        # The check ran with the token from the security code and no account yet.
        assert _login_data(validate)[CONF_LOGIN_DATA] == {"tfa_token": "test"}
        assert CONF_ACCOUNT not in _login_data(validate)

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ACCOUNT: "1"}
        )

        # The pick was tried out before the flow went on.
        assert _login_data(validate)[CONF_ACCOUNT] == SECOND.as_dict()
        assert validate.call_count == 3

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "backfill_options"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EXTENDED_BACKFILL: False, CONF_EXTENDED_COST_BACKFILL: False},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COST_MODE: COST_MODE_RATE_8}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ACCOUNT] == SECOND.as_dict()
    assert result["data"][CONF_LOGIN_DATA] == {"tfa_token": "test"}


async def test_setup_without_a_choice_to_make_stores_no_account(
    hass: HomeAssistant,
    mock_tfa_handler: AsyncMock,
    mock_setup_entry: AsyncMock,
    user_input: dict,
) -> None:
    """A login that needs no choice goes on as before."""
    with patch(
        VALIDATE_LOGIN,
        side_effect=[MfaChallenge("TFA Required", mock_tfa_handler), None],
    ):
        result = await _user_flow_to_tfa_code(hass, user_input)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TFA_CODE: "123456"}
        )

    assert result["step_id"] == "backfill_options"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EXTENDED_BACKFILL: False, CONF_EXTENDED_COST_BACKFILL: False},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COST_MODE: COST_MODE_RATE_8}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_ACCOUNT not in result["data"]


@pytest.mark.parametrize(
    "problem",
    [
        ApiException("Unexpected response", url="https://example.invalid"),
        CannotConnect("Network down"),
        InvalidAuth("Refused"),
        MfaChallenge("TFA Required", MagicMock()),
    ],
)
async def test_failed_account_check_does_not_block_setup(
    hass: HomeAssistant,
    mock_tfa_handler: AsyncMock,
    user_input: dict,
    problem: Exception,
) -> None:
    """The security code is used up, so a failed check lets setup carry on."""
    with patch(
        VALIDATE_LOGIN,
        side_effect=[MfaChallenge("TFA Required", mock_tfa_handler), problem],
    ):
        result = await _user_flow_to_tfa_code(hass, user_input)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TFA_CODE: "123456"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "backfill_options"


async def test_choice_needed_at_the_first_step(
    hass: HomeAssistant, user_input: dict
) -> None:
    """A login that works without a security code can still need a choice."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(VALIDATE_LOGIN, side_effect=[_choice_needed(FIRST, SECOND), None]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
        assert result["step_id"] == "select_account"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ACCOUNT: "0"}
        )

    assert result["step_id"] == "backfill_options"


# ---------------------------------------------------------------------------
# The picker
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("problem", "error"),
    [
        (CannotConnect("Network down"), "cannot_connect"),
        (ApiException("Refused", url="https://example.invalid"), "unknown"),
        (InvalidAuth("Refused"), "invalid_auth"),
        (MfaChallenge("TFA Required", MagicMock()), "invalid_auth"),
    ],
)
async def test_picker_shows_an_error_and_can_be_retried(
    hass: HomeAssistant, user_input: dict, problem: Exception, error: str
) -> None:
    """A login that fails with the picked account keeps the picker open."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        VALIDATE_LOGIN, side_effect=[_choice_needed(FIRST, SECOND), problem, None]
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ACCOUNT: "0"}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "select_account"
        assert result["errors"] == {"base": error}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ACCOUNT: "0"}
        )

    assert result["step_id"] == "backfill_options"


async def test_picker_offers_the_new_list_when_the_pick_is_gone(
    hass: HomeAssistant, user_input: dict
) -> None:
    """If the picked account stops being open, the current accounts are offered."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        VALIDATE_LOGIN,
        side_effect=[
            _choice_needed(FIRST, SECOND),
            _choice_needed(SECOND, THIRD),
            None,
        ],
    ) as validate:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ACCOUNT: "0"}
        )

        assert result["step_id"] == "select_account"
        assert result["errors"] == {"base": "account_unavailable"}
        assert result["data_schema"].schema[CONF_ACCOUNT].container == {
            "0": "2 OAK ST (account ending 8-2071)",
            "1": "3 PINE RD (account ending 5-6638)",
        }

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ACCOUNT: "1"}
        )

        assert _login_data(validate)[CONF_ACCOUNT] == THIRD.as_dict()

    assert result["step_id"] == "backfill_options"


# ---------------------------------------------------------------------------
# Signing in again
# ---------------------------------------------------------------------------


async def test_reauth_asks_which_account_and_updates_the_entry(
    hass: HomeAssistant, mock_setup_entry: AsyncMock, user_input_with_login_data: dict
) -> None:
    """A second account opened after setup leads to the picker on sign-in."""
    entry = MockConfigEntry(domain=DOMAIN, data=user_input_with_login_data)
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    credentials = {
        CONF_USERNAME: user_input_with_login_data[CONF_USERNAME],
        CONF_PASSWORD: user_input_with_login_data[CONF_PASSWORD],
    }

    with patch(VALIDATE_LOGIN, side_effect=[_choice_needed(FIRST, SECOND), None]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], credentials
        )
        assert result["step_id"] == "select_account"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ACCOUNT: "0"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_ACCOUNT] == FIRST.as_dict()
    assert entry.data[CONF_LOGIN_DATA] == {"tfa_token": "test"}


async def test_reauth_with_tfa_asks_which_account(
    hass: HomeAssistant,
    mock_tfa_handler: AsyncMock,
    mock_setup_entry: AsyncMock,
    user_input: dict,
) -> None:
    """The picker also follows a security code when signing in again."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={**user_input, CONF_ACCOUNT: THIRD.as_dict()}
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    mock_tfa_handler.async_get_tfa_options.return_value = []

    with patch(
        VALIDATE_LOGIN,
        side_effect=[
            MfaChallenge("TFA Required", mock_tfa_handler),
            _choice_needed(FIRST, SECOND),
            None,
        ],
    ) as validate:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
        assert result["step_id"] == "tfa_code"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TFA_CODE: "123456"}
        )
        assert result["step_id"] == "select_account"
        # The account stored earlier is offered to the login first.
        assert validate.call_args_list[1].args[1][CONF_ACCOUNT] == THIRD.as_dict()

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ACCOUNT: "1"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_ACCOUNT] == SECOND.as_dict()


# ---------------------------------------------------------------------------
# Passing the stored account to the login
# ---------------------------------------------------------------------------


async def test_validate_login_passes_the_stored_account(hass: HomeAssistant) -> None:
    """The login check hands a stored account to the API client."""
    data = {
        CONF_USERNAME: "test",
        CONF_PASSWORD: "test",
        CONF_ACCOUNT: SECOND.as_dict(),
    }

    with patch("custom_components.dominionsc.config_flow.DominionSC") as api:
        api.return_value.async_login = AsyncMock()
        await _validate_login(hass, data)

    assert api.call_args.args[4] == SECOND


async def test_validate_login_without_a_stored_account(hass: HomeAssistant) -> None:
    """Without a stored account the API client is left to decide."""
    with patch("custom_components.dominionsc.config_flow.DominionSC") as api:
        api.return_value.async_login = AsyncMock()
        await _validate_login(hass, {CONF_USERNAME: "test", CONF_PASSWORD: "test"})

    assert api.call_args.args[4] is None


@pytest.mark.parametrize(
    ("stored", "expected"), [(SECOND.as_dict(), SECOND), (None, None)]
)
async def test_coordinator_passes_the_stored_account(
    hass: HomeAssistant,
    user_input_with_login_data: dict,
    stored: dict | None,
    expected: AccountChoice | None,
) -> None:
    """Each scheduled login uses the account stored in the entry."""
    data = dict(user_input_with_login_data)
    if stored is not None:
        data[CONF_ACCOUNT] = stored
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)

    with patch("custom_components.dominionsc.coordinator.DominionSC") as api:
        DominionSCCoordinator(hass, entry)

    assert api.call_args.args[4] == expected
