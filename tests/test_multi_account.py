"""Tests for logins that hold more than one Dominion account."""

import json
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest

from custom_components.dominionsc.dominionsc_api.dominionsc import (
    AccountChoice,
    DominionSC,
    DominionSCURLHandler,
)
from custom_components.dominionsc.dominionsc_api.exceptions import (
    AccountSelectionRequired,
    ApiException,
    InvalidAuth,
)

VERIFICATION_PAGE = (
    '<input name="__RequestVerificationToken" type="hidden" value="token" />'
)
SESSION = json.dumps(
    {
        "payload": {
            "tokenDetails": {"accessToken": "access123"},
            "userProfileDetails": {"userId": "user456"},
            "userTypeDetails": {
                "measurementToUserTypeMappings": [{"measurementType": "ELECTRIC"}]
            },
        }
    }
)
SELECTED = json.dumps(
    {"data": True, "fieldMessages": [], "pageMessage": None, "responseType": 0}
)
SELECT_URL = (
    "https://account.dominionenergysc.com"
    "/fusionapi/AccountManagementWebApi/SelectAccount/"
)

# Every value below is made up.
CUSTOMER = "JANE DOE"
OPEN_ACCOUNT = {
    "accountNumberFormatted": "7-4410",
    "accountNumber": "encrypted-open",
    "accountStatus": "A",
    "closedAccount": False,
    "finalBill": False,
    "serviceAddress": "1 MAIN ST APT 110",
}
CLOSED_ACCOUNT = {
    "accountNumberFormatted": "3-9052",
    "accountNumber": "encrypted-closed",
    "accountStatus": "B",
    "closedAccount": True,
    "finalBill": True,
    "serviceAddress": "1 MAIN ST APT 112",
}
OTHER_OPEN_ACCOUNT = {
    "accountNumberFormatted": "8-2071",
    "accountNumber": "encrypted-other",
    "accountStatus": "A",
    "closedAccount": False,
    "finalBill": False,
    "serviceAddress": "2 OAK ST",
}
OPEN_LOADED = "1 MAIN ST APT 110 (*-****-***7-4410)"
CLOSED_LOADED = "1 MAIN ST APT 112 (*-****-***3-9052)"
OTHER_LOADED = "2 OAK ST (*-****-***8-2071)"
OPEN_CHOICE = AccountChoice("7-4410", "1 MAIN ST APT 110")
CLOSED_CHOICE = AccountChoice("3-9052", "1 MAIN ST APT 112")
OTHER_CHOICE = AccountChoice("8-2071", "2 OAK ST")
PERSONAL_DETAILS = (
    CUSTOMER,
    "MAIN ST",
    "OAK ST",
    "4410",
    "9052",
    "2071",
    "encrypted-open",
    "encrypted-closed",
    "encrypted-other",
)


def _listing(*accounts: dict[str, Any], single: bool = False) -> str:
    return json.dumps(
        {
            "data": {
                "singleAccount": single,
                "customerName": CUSTOMER,
                "accountListing": list(accounts),
            }
        }
    )


def _init_account(loaded: str) -> str:
    return json.dumps({"data": {"account": {"serviceAddressAndAccountNo": loaded}}})


def _responses(
    listing: str, init_account: str, select_account: str | None = SELECTED
) -> list[str]:
    """Return what each request of a login answers, in order."""
    # select_account=None is for a login that sends no SelectAccount request.
    selection = [] if select_account is None else [select_account]
    return [
        VERIFICATION_PAGE,
        '{"data": {"status": "twoFA"}}',
        '{"data": true}',
        VERIFICATION_PAGE,
        listing,
        *selection,
        init_account,
        '{"data": {"payload": "encrypted"}}',
        SESSION,
    ]


@pytest.fixture
def client() -> DominionSC:
    """Create a client with a mocked session."""
    return DominionSC(
        session=Mock(spec=aiohttp.ClientSession),
        username="user",
        password="pass",  # noqa: S106
        login_data={"tfa_token": "valid"},
    )


async def _login_with_calls(
    client: DominionSC, responses: list[str], account: AccountChoice | None = None
) -> tuple[tuple[Any, Any, Any], AsyncMock]:
    call_api = AsyncMock(side_effect=responses)
    with patch.object(DominionSCURLHandler, "call_api", new=call_api):
        result = await client._async_login_internal(  # noqa: SLF001
            client.session, "user", "pass", {"tfa_token": "valid"}, account
        )
    return result, call_api


async def _login(client: DominionSC, responses: list[str]) -> tuple[Any, Any, Any]:
    result, _ = await _login_with_calls(client, responses)
    return result


async def _failed_login_calls(
    client: DominionSC,
    responses: list[str],
    error: type[Exception],
    match: str,
    account: AccountChoice | None = None,
) -> tuple[Exception, AsyncMock]:
    call_api = AsyncMock(side_effect=responses)
    with (
        patch.object(DominionSCURLHandler, "call_api", new=call_api),
        pytest.raises(error, match=match) as err,
    ):
        await client._async_login_internal(  # noqa: SLF001
            client.session, "user", "pass", {"tfa_token": "valid"}, account
        )
    return err.value, call_api


def _selected(call_api: AsyncMock) -> str:
    """Return the encrypted number the login asked Dominion to select."""
    posts = [call for call in call_api.call_args_list if call.args[1] == SELECT_URL]
    assert len(posts) == 1
    return posts[0].args[3]["EncryptedAccountNumber"]


def _urls(call_api: AsyncMock) -> list[str]:
    return [call.args[1] for call in call_api.call_args_list]


def _assert_no_personal_details(message: str) -> None:
    for detail in PERSONAL_DETAILS:
        assert detail not in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "accounts",
    [(OPEN_ACCOUNT, CLOSED_ACCOUNT), (CLOSED_ACCOUNT, OPEN_ACCOUNT)],
)
async def test_one_open_and_one_closed_account_logs_in(
    client: DominionSC, accounts: tuple[dict[str, Any], ...]
) -> None:
    """The open account is used, wherever it sits in the listing."""
    responses = _responses(_listing(*accounts), _init_account(OPEN_LOADED))

    access_token, user_id, result = await _login(client, responses)

    assert access_token == "access123"  # noqa: S105
    assert user_id == "user456"
    assert result[0] == ["ELECTRIC"]
    assert result[1] == OPEN_LOADED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "accounts",
    [(OPEN_ACCOUNT, CLOSED_ACCOUNT), (CLOSED_ACCOUNT, OPEN_ACCOUNT)],
)
async def test_open_account_is_selected_before_it_is_loaded(
    client: DominionSC, accounts: tuple[dict[str, Any], ...]
) -> None:
    """The open account is selected the way the website selects it."""
    responses = _responses(_listing(*accounts), _init_account(OPEN_LOADED))

    _, call_api = await _login_with_calls(client, responses)

    calls = call_api.call_args_list
    assert len(calls) == len(responses)
    assert "/AccountManagementWebApi/GetAccountListing/" in calls[4].args[1]

    method, url, headers, body = calls[5].args
    assert method == "post"
    assert url == SELECT_URL
    assert body == {"EncryptedAccountNumber": "encrypted-open", "_df": ""}
    assert headers["__RequestVerificationToken"] == "token"
    assert headers["X-Requested-With"] == "XMLHttpRequest"
    assert headers["Origin"] == "https://account.dominionenergysc.com"

    assert calls[6].args[0] == "get"
    assert "/AccountSummaryWebApi/InitAccount/" in calls[6].args[1]
    assert "Origin" not in calls[6].args[2]


@pytest.mark.asyncio
async def test_refused_selection_stops_login(client: DominionSC) -> None:
    """The login stops if Dominion does not confirm the selection."""
    refused = json.dumps({"data": False, "pageMessage": f"Sorry {CUSTOMER}"})
    responses = _responses(
        _listing(OPEN_ACCOUNT, CLOSED_ACCOUNT), _init_account(OPEN_LOADED), refused
    )

    error, call_api = await _failed_login_calls(
        client, responses, ApiException, "did not accept the open account"
    )

    assert error.response_text is None
    _assert_no_personal_details(str(error))
    assert not any("InitAccount" in url for url in _urls(call_api))


@pytest.mark.asyncio
async def test_unreadable_selection_response_stops_login(client: DominionSC) -> None:
    """The login stops if the answer to the selection cannot be read."""
    responses = _responses(
        _listing(OPEN_ACCOUNT, CLOSED_ACCOUNT),
        _init_account(OPEN_LOADED),
        f"<html>{CUSTOMER}</html>",
    )

    error, call_api = await _failed_login_calls(
        client, responses, ApiException, "Unable to decode SelectAccount"
    )

    assert error.response_text is None
    _assert_no_personal_details(str(error))
    assert not any("InitAccount" in url for url in _urls(call_api))


@pytest.mark.asyncio
async def test_open_account_without_identifier_stops_login(client: DominionSC) -> None:
    """Nothing is sent if the open account cannot be named."""
    unnamed = {**OPEN_ACCOUNT, "accountNumber": ""}
    responses = _responses(
        _listing(unnamed, CLOSED_ACCOUNT), _init_account(OPEN_LOADED)
    )

    error, call_api = await _failed_login_calls(
        client, responses, ApiException, "no identifier"
    )

    _assert_no_personal_details(str(error))
    assert not any("SelectAccount" in url for url in _urls(call_api))
    assert not any("InitAccount" in url for url in _urls(call_api))


@pytest.mark.asyncio
async def test_closed_account_loaded_instead_of_open_one_stops_login(
    client: DominionSC,
) -> None:
    """The login stops if Dominion loads the closed account."""
    responses = _responses(
        _listing(OPEN_ACCOUNT, CLOSED_ACCOUNT), _init_account(CLOSED_LOADED)
    )

    with pytest.raises(ApiException, match="different account") as err:
        await _login(client, responses)

    assert err.value.response_text is None
    _assert_no_personal_details(str(err.value.args[0]))


@pytest.mark.asyncio
async def test_unrecognised_loaded_account_continues(client: DominionSC) -> None:
    """An unfamiliar InitAccount format does not block the login."""
    responses = _responses(
        _listing(OPEN_ACCOUNT, CLOSED_ACCOUNT), _init_account("SOMETHING ELSE")
    )

    access_token, _, result = await _login(client, responses)

    assert access_token == "access123"  # noqa: S105
    assert result[1] == "SOMETHING ELSE"


@pytest.mark.asyncio
async def test_no_open_account_is_refused(client: DominionSC) -> None:
    """A login whose accounts are all closed is refused without leaking details."""
    accounts = (CLOSED_ACCOUNT, {**CLOSED_ACCOUNT, "serviceAddress": "2 OAK ST"})
    responses = _responses(_listing(*accounts), _init_account(OPEN_LOADED))

    error, call_api = await _failed_login_calls(
        client, responses, InvalidAuth, "found 0 open"
    )

    assert not isinstance(error, AccountSelectionRequired)
    message = str(error)
    assert "2 account(s)" in message
    _assert_no_personal_details(message)
    assert not any("SelectAccount" in url for url in _urls(call_api))


@pytest.mark.asyncio
async def test_several_open_accounts_need_a_choice(client: DominionSC) -> None:
    """Several open accounts are offered as choices and nothing is selected."""
    responses = _responses(
        _listing(OPEN_ACCOUNT, CLOSED_ACCOUNT, OTHER_OPEN_ACCOUNT),
        _init_account(OPEN_LOADED),
    )

    error, call_api = await _failed_login_calls(
        client, responses, AccountSelectionRequired, "found 2 open"
    )

    # Callers that only know InvalidAuth still treat this as a failed login.
    assert isinstance(error, InvalidAuth)
    assert error.accounts == [OPEN_CHOICE, OTHER_CHOICE]
    message = str(error)
    assert "3 account(s)" in message
    _assert_no_personal_details(message)
    assert not any("SelectAccount" in url for url in _urls(call_api))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("choice", "loaded", "encrypted"),
    [
        (OPEN_CHOICE, OPEN_LOADED, "encrypted-open"),
        (OTHER_CHOICE, OTHER_LOADED, "encrypted-other"),
    ],
)
async def test_chosen_account_is_used_among_open_accounts(
    client: DominionSC, choice: AccountChoice, loaded: str, encrypted: str
) -> None:
    """The chosen account is selected when several are open."""
    responses = _responses(
        _listing(OPEN_ACCOUNT, OTHER_OPEN_ACCOUNT, CLOSED_ACCOUNT),
        _init_account(loaded),
    )

    (_, _, result), call_api = await _login_with_calls(client, responses, choice)

    assert result[1] == loaded
    assert _selected(call_api) == encrypted


@pytest.mark.asyncio
async def test_closed_choice_falls_back_to_the_one_open_account(
    client: DominionSC,
) -> None:
    """Once the chosen account has closed, a single open account is used."""
    responses = _responses(
        _listing(OPEN_ACCOUNT, CLOSED_ACCOUNT), _init_account(OPEN_LOADED)
    )

    (_, _, result), call_api = await _login_with_calls(client, responses, CLOSED_CHOICE)

    assert result[1] == OPEN_LOADED
    assert _selected(call_api) == "encrypted-open"


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", [CLOSED_CHOICE, AccountChoice("0-0000", "9 ELM ST")])
async def test_unusable_choice_among_open_accounts_needs_a_new_choice(
    client: DominionSC, choice: AccountChoice
) -> None:
    """A closed or missing choice is not replaced by a guess."""
    responses = _responses(
        _listing(OPEN_ACCOUNT, OTHER_OPEN_ACCOUNT, CLOSED_ACCOUNT),
        _init_account(OPEN_LOADED),
    )

    error, call_api = await _failed_login_calls(
        client, responses, AccountSelectionRequired, "found 2 open", choice
    )

    assert error.accounts == [OPEN_CHOICE, OTHER_CHOICE]
    assert not any("SelectAccount" in url for url in _urls(call_api))


@pytest.mark.asyncio
async def test_choice_is_told_apart_by_address_when_endings_match(
    client: DominionSC,
) -> None:
    """Two open accounts with the same ending are told apart by address."""
    ending = OPEN_ACCOUNT["accountNumberFormatted"]
    twin = {**OTHER_OPEN_ACCOUNT, "accountNumberFormatted": ending}
    loaded = "2 OAK ST (*-****-***7-4410)"
    responses = _responses(_listing(OPEN_ACCOUNT, twin), _init_account(loaded))

    (_, _, result), call_api = await _login_with_calls(
        client, responses, AccountChoice(ending, "2 OAK ST")
    )

    assert result[1] == loaded
    assert _selected(call_api) == "encrypted-other"


@pytest.mark.asyncio
async def test_same_ending_and_unknown_address_needs_a_new_choice(
    client: DominionSC,
) -> None:
    """A choice that fits two accounts equally is not guessed at."""
    ending = OPEN_ACCOUNT["accountNumberFormatted"]
    twin = {**OTHER_OPEN_ACCOUNT, "accountNumberFormatted": ending}
    responses = _responses(_listing(OPEN_ACCOUNT, twin), _init_account(OPEN_LOADED))

    error, _ = await _failed_login_calls(
        client,
        responses,
        AccountSelectionRequired,
        "found 2 open",
        AccountChoice(ending, "9 ELM ST"),
    )

    assert len(error.accounts) == 2


@pytest.mark.asyncio
async def test_client_logs_in_with_the_account_it_was_given() -> None:
    """The account passed to the client is the one its login selects."""
    client = DominionSC(
        session=Mock(spec=aiohttp.ClientSession),
        username="user",
        password="pass",  # noqa: S106
        login_data={"tfa_token": "valid"},
        account=OTHER_CHOICE,
    )
    responses = _responses(
        _listing(OPEN_ACCOUNT, OTHER_OPEN_ACCOUNT), _init_account(OTHER_LOADED)
    )
    call_api = AsyncMock(side_effect=responses)

    with patch.object(DominionSCURLHandler, "call_api", new=call_api):
        await client.async_login()

    assert _selected(call_api) == "encrypted-other"
    assert client.accounts[1] == OTHER_LOADED


def test_account_choice_is_stored_and_rebuilt() -> None:
    """A choice survives being stored, and junk is not taken for a choice."""
    assert AccountChoice.from_dict(OPEN_CHOICE.as_dict()) == OPEN_CHOICE
    assert OPEN_CHOICE.as_dict() == {
        "account_ending": "7-4410",
        "service_address": "1 MAIN ST APT 110",
    }

    junk = (
        None,
        "7-4410",
        {},
        {"account_ending": "7-4410"},
        {"account_ending": 74410, "service_address": "1 MAIN ST APT 110"},
        {"account_ending": "", "service_address": ""},
    )
    for value in junk:
        assert AccountChoice.from_dict(value) is None


def test_account_choice_label() -> None:
    """The label shows what the website shows for an account."""
    assert OPEN_CHOICE.label == "1 MAIN ST APT 110 (account ending 7-4410)"
    assert AccountChoice("7-4410", "").label == "Account ending 7-4410"
    assert AccountChoice("", "2 OAK ST").label == "2 OAK ST"


@pytest.mark.asyncio
async def test_unreadable_account_summary_hides_the_response(
    client: DominionSC,
) -> None:
    """A failed InitAccount on a multi-account login does not echo the response."""
    responses = _responses(
        _listing(OPEN_ACCOUNT, CLOSED_ACCOUNT),
        json.dumps({"data": {"customerName": CUSTOMER}}),
    )

    with pytest.raises(ApiException, match="Unable to load the open account") as err:
        await _login(client, responses)

    assert err.value.response_text is None
    _assert_no_personal_details(str(err.value.args[0]))


@pytest.mark.asyncio
async def test_single_account_login_is_unchanged(client: DominionSC) -> None:
    """A login with one account behaves as before and selects nothing."""
    responses = _responses(
        _listing(single=True), _init_account("ACC123"), select_account=None
    )

    (access_token, user_id, result), call_api = await _login_with_calls(
        client, responses
    )

    assert access_token == "access123"  # noqa: S105
    assert user_id == "user456"
    assert result[1] == "ACC123"
    assert len(call_api.call_args_list) == len(responses)
    assert not any("SelectAccount" in url for url in _urls(call_api))


def test_loaded_account_is_matched_in_the_format_dominion_uses() -> None:
    """The address and masked number InitAccount returns point at one account."""
    listing = {"accountListing": [OPEN_ACCOUNT, CLOSED_ACCOUNT]}

    find = DominionSC._find_loaded_account  # noqa: SLF001

    assert find(OPEN_LOADED, listing) is OPEN_ACCOUNT
    assert find(CLOSED_LOADED, listing) is CLOSED_ACCOUNT


def test_loaded_account_with_a_shared_ending_is_matched_by_address() -> None:
    """An ending two accounts share does not hide which one was loaded."""
    ending = OPEN_ACCOUNT["accountNumberFormatted"]
    twin = {**OTHER_OPEN_ACCOUNT, "accountNumberFormatted": ending}
    listing = {"accountListing": [OPEN_ACCOUNT, twin]}

    find = DominionSC._find_loaded_account  # noqa: SLF001

    assert find("2 OAK ST (*-****-***7-4410)", listing) is twin
    assert find(OPEN_LOADED, listing) is OPEN_ACCOUNT


def test_similar_addresses_are_told_apart() -> None:
    """APT 11 is not mistaken for APT 110."""
    short = {**OPEN_ACCOUNT, "serviceAddress": "1 MAIN ST APT 11"}
    listing = {"accountListing": [short, CLOSED_ACCOUNT]}

    find = DominionSC._find_loaded_account  # noqa: SLF001

    assert find("1 MAIN ST APT 11, ANYTOWN", listing) is short
    assert find("1 MAIN ST APT 112, ANYTOWN", listing) is CLOSED_ACCOUNT
    assert find("1 MAIN ST APT 110, ANYTOWN", listing) is None
