# -*- coding: utf-8 -*-
"""
Phase 10.5 -- Virtual NAO simulator package.

Top-level convenience exports so callers (live_nao, scenarios, e2e tests)
don't need to know which submodule a class lives in.

Public surface
--------------
    install_into_sys_modules     -- wire the fake naoqi/qi into sys.modules
    uninstall                    -- reverse sys.modules wiring
    set_pcm_source               -- configure the PCM source the fake
                                    ALAudioDevice pulls from
    get_pcm_source               -- introspect current source
    reset_state                  -- clear all fake registries between runs
    get_service                  -- look up a registered fake service
    list_services                -- list all registered services
    EchoSimulator                -- inject delayed speaker echo into mic
    LedsConsoleRenderer          -- ANSI terminal renderer for LED state
    ALProxy                      -- the fake ALProxy class (for direct use
                                    in tests; usually accessed via
                                    ``import naoqi`` after install)
    ALModule                     -- ALModule base class for ALAudioDevice
                                    subscribers

Typical usage
-------------
    from sim import (
        install_into_sys_modules,
        EchoSimulator,
        LedsConsoleRenderer,
        set_pcm_source,
    )

    leds = LedsConsoleRenderer()
    echo = EchoSimulator(delay_ms=80, gain=0.10, enabled=False)

    install_into_sys_modules(
        echo_sim=echo,
        leds_renderer=leds,
        on_event=lambda kind, data: print(kind, data),
    )

    # Now `import naoqi` and `import qi` resolve to our fakes.
    import nao.audio_module  # works without a real robot
"""
from sim.echo_sim import EchoSimulator
from sim.leds_console import LedsConsoleRenderer
from sim.fake_naoqi import (
    ALModule,
    ALProxy,
    FakeBroker,
    get_pcm_source,
    get_service,
    install_into_sys_modules,
    list_services,
    reset_state,
    set_pcm_source,
    uninstall,
)

__all__ = [
    # Hook installation
    "install_into_sys_modules",
    "uninstall",
    "reset_state",
    # PCM source control
    "set_pcm_source",
    "get_pcm_source",
    # Registry introspection
    "get_service",
    "list_services",
    # Core classes (also exposed via `import naoqi` after install)
    "ALModule",
    "ALProxy",
    "FakeBroker",
    # Auxiliary tools
    "EchoSimulator",
    "LedsConsoleRenderer",
]
