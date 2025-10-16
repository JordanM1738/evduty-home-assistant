import asyncio
from datetime import timedelta
from http import HTTPStatus

from evdutyapi import EVDutyApi, Terminal, EVDutyApiInvalidCredentialsError, EVDutyApiError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, LOGGER


# https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
class EVDutyCoordinator(DataUpdateCoordinator):
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, api: EVDutyApi) -> None:
        super().__init__(hass=hass, config_entry=config_entry, logger=LOGGER, name=DOMAIN,
                         update_interval=timedelta(seconds=60))
        self.api = api
        self.active_sessions = {}

    async def _async_update_data(self) -> dict[str, Terminal]:
        try:
            async with asyncio.timeout(10):
                stations = await self.api.async_get_stations()
                return {terminal.id: terminal for station in stations for terminal in station.terminals}
        except EVDutyApiInvalidCredentialsError as error:
            raise ConfigEntryAuthFailed from error
        except EVDutyApiError as error:
            if error.status == HTTPStatus.UNAUTHORIZED:
                LOGGER.debug(f'Simultaneous EVduty account usage. Returning last data: {self.data}')
                return self.data
            if error.status == HTTPStatus.BAD_GATEWAY:
                return self.data
            else:
                raise ConnectionError from error

    async def async_set_terminal_max_charging_current(self, terminal: Terminal, current: int):
        try:
            async with asyncio.timeout(10):
                await self.api.async_set_terminal_max_charging_current(terminal, current)
                await self.async_request_refresh()
        except EVDutyApiInvalidCredentialsError as error:
            raise ConfigEntryAuthFailed from error
        except EVDutyApiError as error:
            raise ConnectionError from error

    async def async_start_session(self, terminal: Terminal, connector_id: int = 1,
                                   target_duration: int = 86400, target_energy: int = 80000,
                                   target_percentage: int = 100):
        """Start a charging session with configurable parameters."""
        try:
            async with asyncio.timeout(10):
                session_response = await self.api.async_start_session(
                    terminal=terminal,
                    connector_id=connector_id,
                    target_duration=target_duration,
                    target_energy=target_energy,
                    target_percentage=target_percentage
                )
                LOGGER.info(f"Started session {session_response.id} on terminal {terminal.id}")
                self.active_sessions[terminal.id] = session_response.id
                await self.async_request_refresh()
                return session_response
        except EVDutyApiInvalidCredentialsError as error:
            LOGGER.error(f"Invalid credentials when starting session: {error}")
            raise ConfigEntryAuthFailed from error
        except EVDutyApiError as error:
            LOGGER.error(f"API error when starting session: {error}")
            raise ConnectionError from error
    
    async def async_cancel_session(self, session_id: str, terminal_id: str = None):
        """Cancel an active charging session."""
        try:
            async with asyncio.timeout(10):
                LOGGER.debug(f"Calling API to cancel session {session_id}")
                await self.api.async_cancel_session(session_id)
                LOGGER.info(f"API call successful for cancelling session {session_id}")
                if terminal_id and terminal_id in self.active_sessions:
                    del self.active_sessions[terminal_id]
                await asyncio.sleep(2)
                await self.async_request_refresh()
                LOGGER.debug(f"Data refreshed after cancelling session {session_id}")
        except EVDutyApiInvalidCredentialsError as error:
            LOGGER.error(f"Invalid credentials when cancelling session: {error}")
            raise ConfigEntryAuthFailed from error
        except EVDutyApiError as error:
            LOGGER.error(f"API error when cancelling session {session_id}: {error}")
            raise ConnectionError from error
