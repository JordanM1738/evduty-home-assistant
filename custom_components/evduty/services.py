"""EVduty charging session control services."""
from evdutyapi import Terminal
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import DOMAIN, LOGGER
from .coordinator import EVDutyCoordinator

SERVICE_START_SESSION = "start_session"
SERVICE_CANCEL_SESSION = "cancel_session"

START_SESSION_SCHEMA = cv.make_entity_service_schema({
    vol.Optional("connector_id", default=1): cv.positive_int,
    vol.Optional("target_duration", default=86400): cv.positive_int,
    vol.Optional("target_energy", default=80000): cv.positive_int,
    vol.Optional("target_percentage", default=100): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
})

CANCEL_SESSION_SCHEMA = cv.make_entity_service_schema({})


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for EVduty integration."""
    
    async def handle_start_session(call: ServiceCall) -> None:
        """Handle the start_session service call."""
        # Log raw call data for debugging
        LOGGER.debug(f"Service call data: {call.data}")
        
        # Extract device_id - it might be directly in call.data or in a target key
        device_ids = call.data.get("device_id")
        if not device_ids:
            target = call.data.get("target", {})
            device_ids = target.get("device_id", [])
        
        if not device_ids:
            LOGGER.error(f"No device specified in service call. Available keys: {list(call.data.keys())}")
            return
        
        # Handle first device (services typically target one device at a time)
        if isinstance(device_ids, list):
            device_id = device_ids[0]
        else:
            device_id = device_ids
        
        connector_id = call.data.get("connector_id", 1)
        target_duration = call.data.get("target_duration", 86400)
        target_energy = call.data.get("target_energy", 80000)
        target_percentage = call.data.get("target_percentage", 100)
        
        LOGGER.info(f"Service start_session called for device {device_id}")
        LOGGER.debug(f"Parameters: connector={connector_id}, duration={target_duration}, "
                    f"energy={target_energy}, percentage={target_percentage}")
        
        terminal, coordinator = await _get_terminal_from_device_id(hass, device_id)
        
        if terminal is None:
            LOGGER.error(f"Terminal not found for device_id: {device_id}")
            return
        
        if not terminal.access_mode or terminal.access_mode.value != 'remote':
            LOGGER.warning(f"Terminal {device_id} is not in remote access mode. "
                          f"Session control not available (mode: {terminal.access_mode})")
            return
        
        try:
            session_response = await coordinator.async_start_session(
                terminal=terminal,
                connector_id=connector_id,
                target_duration=target_duration,
                target_energy=target_energy,
                target_percentage=target_percentage
            )
            LOGGER.info(f"Session started successfully: {session_response.id}")
        except Exception as e:
            LOGGER.error(f"Failed to start session on {device_id}: {e}")
            raise
    
    async def handle_cancel_session(call: ServiceCall) -> None:
        """Handle the cancel_session service call."""
        # Log raw call data for debugging
        LOGGER.debug(f"Service call data: {call.data}")
        
        # Extract device_id - it might be directly in call.data or in a target key
        device_ids = call.data.get("device_id")
        if not device_ids:
            target = call.data.get("target", {})
            device_ids = target.get("device_id", [])
        
        if not device_ids:
            LOGGER.error(f"No device specified in service call. Available keys: {list(call.data.keys())}")
            return
        
        # Handle first device
        if isinstance(device_ids, list):
            device_id = device_ids[0]
        else:
            device_id = device_ids
        
        LOGGER.info(f"Service cancel_session called for device {device_id}")
        
        terminal, coordinator = await _get_terminal_from_device_id(hass, device_id)
        if terminal is None:
            LOGGER.error(f"Terminal not found for device_id: {device_id}")
            return

        # Force refresh before checking session_id
        await coordinator.async_request_refresh()
        terminal = coordinator.data.get(terminal.id)
        LOGGER.debug(f"After refresh: session={terminal.session}, session_id={terminal.session.session_id if terminal.session else None}")

        # Try to get session_id from terminal first, then from stored sessions
        session_id = None
        if terminal.session and terminal.session.session_id:
            session_id = terminal.session.session_id
            LOGGER.info(f"Using session_id from terminal: {session_id}")
        elif terminal.id in coordinator.active_sessions:
            session_id = coordinator.active_sessions[terminal.id]
            LOGGER.info(f"Using stored session_id for terminal {terminal.id}: {session_id}")
        
        if not session_id:
            LOGGER.error(f"No session_id found on terminal {device_id} after refresh and no stored session. Session: {terminal.session}")
            return

        LOGGER.info(f"Attempting to cancel session {session_id} on terminal {terminal.id}")
        try:
            await coordinator.async_cancel_session(session_id, terminal.id)
            LOGGER.info(f"Session {session_id} cancelled successfully")
        except Exception as e:
            LOGGER.error(f"Failed to cancel session {session_id}: {e}")
            raise
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_START_SESSION,
        handle_start_session,
        schema=START_SESSION_SCHEMA,
    )
    
    hass.services.async_register(
        DOMAIN,
        SERVICE_CANCEL_SESSION,
        handle_cancel_session,
        schema=CANCEL_SESSION_SCHEMA,
    )
    
    LOGGER.info("EVduty services registered successfully")


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload EVduty services."""
    hass.services.async_remove(DOMAIN, SERVICE_START_SESSION)
    hass.services.async_remove(DOMAIN, SERVICE_CANCEL_SESSION)
    LOGGER.info("EVduty services unloaded")


async def _get_terminal_from_device_id(hass: HomeAssistant, device_id: str) -> tuple[Terminal | None, EVDutyCoordinator | None]:
    """Get terminal and coordinator from Home Assistant device_id."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)
    
    if not device:
        LOGGER.error(f"Device not found in registry: {device_id}")
        return None, None
    
    # Find terminal ID from device identifiers
    terminal_id = None
    for identifier in device.identifiers:
        if identifier[0] == DOMAIN:
            terminal_id = identifier[1]
            break
    
    if not terminal_id:
        LOGGER.error(f"No EVduty terminal ID found for device: {device_id}")
        return None, None
    
    # Find coordinator and terminal
    for entry_id, coordinator in hass.data[DOMAIN].items():
        if isinstance(coordinator, EVDutyCoordinator):
            terminal = coordinator.data.get(terminal_id)
            if terminal:
                return terminal, coordinator
    
    LOGGER.error(f"Terminal {terminal_id} not found in coordinator data")
    return None, None
