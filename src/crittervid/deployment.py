"""Deployment details: the camera's time zone and clock offset, the site and the
camera (decision record 0005).

They're saved in <output>/deployment.json the first time they're given, and
read from there on later runs, so every step on an output folder groups days
the same way. An option given on a later run replaces the saved value.
"""
import json
import logging
import os
from dataclasses import replace
from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

from . import log

FILE_NAME = 'deployment.json'
SCHEMA = 1
FIELDS = ('timezone', 'clock_offset', 'site', 'camera')
LABELS = {'timezone': 'time zone', 'clock_offset': 'clock offset', 'site': 'site', 'camera': 'camera'}

_log = log.get('run')


class Invalid(Exception):
    """Deployment details that can't be used: an unknown time zone, or a
    deployment.json that isn't one this version can read."""


def add_options(parser):
    """The deployment options, for each command that takes them."""
    parser.add_argument('--timezone', default=None, metavar='NAME',
                        help="The camera's time zone, such as Europe/Vilnius (an IANA name), for grouping "
                             "footage into days (default: the one saved in the output folder, otherwise "
                             "the computer's)")
    parser.add_argument('--clock-offset', type=int, default=None, metavar='SECONDS',
                        help="Seconds to add to the camera's times when its clock was wrong, such as 75 "
                             "or -3600 (default: the one saved in the output folder, otherwise 0)")
    parser.add_argument('--site', default=None, help='Where the camera was (saved with the outputs)')
    parser.add_argument('--camera', default=None, help='Which camera it was (saved with the outputs)')


def options(config):
    """The deployment options doing what the config says."""
    args = []
    for name in FIELDS:
        value = getattr(config, name)
        if value is not None:
            args += [f"--{name.replace('_', '-')}", str(value)]
    return args


@lru_cache(maxsize=1)
def known_zones():
    """The names of the time zone data's time zones."""
    return frozenset(available_timezones())


def zone(name):
    """The time zone called name, or Invalid. Only the names of the time zone
    data count: on Windows, ZoneInfo also accepts names that are the same file
    there (such as with a trailing space), which other systems would reject."""
    if name not in known_zones():
        raise Invalid(f"unknown time zone '{name}': use an IANA name such as Europe/Vilnius or America/New_York")
    return ZoneInfo(name)


def path(config):
    return config.output_dir / FILE_NAME


def read(config):
    """The saved settings, {} if there are none; Invalid if the file can't be used."""
    file = path(config)
    try:
        with open(file, encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise Invalid(f"{file} can't be read ({e}); fix or delete it") from None
    if not isinstance(data, dict):
        raise Invalid(f"{file} isn't a deployment file; fix or delete it")
    schema = data.get('schema')
    if not isinstance(schema, int) or isinstance(schema, bool):
        raise Invalid(f"{file} has no schema number; fix or delete it")
    if schema > SCHEMA:
        raise Invalid(f"{file} was written by a newer version of CritterVid; update CritterVid to use it")
    saved = {}
    for name in FIELDS:
        value = data.get(name)
        if value is None:
            continue
        if name == 'clock_offset':
            if not isinstance(value, int) or isinstance(value, bool):
                raise Invalid(f"{file}: the clock offset must be a whole number of seconds, not {value!r}")
        elif not isinstance(value, str):
            raise Invalid(f"{file}: the {LABELS[name]} must be text, not {value!r}")
        if name == 'timezone':
            try:
                zone(value)
            except Invalid as e:
                raise Invalid(f"{file}: {e}") from None
        saved[name] = value
    return saved


def write(config, values, set_times):
    """Save values to deployment.json, through a temporary file renamed into place,
    so a run stopped halfway leaves the old file."""
    file = path(config)
    data = {'schema': SCHEMA, **{name: values.get(name) for name in FIELDS}, 'set': set_times}
    tmp = file.with_name(f'{FILE_NAME}.{os.getpid()}.tmp')
    try:
        config.output_dir.mkdir(parents=True, exist_ok=True)
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write('\n')
        os.replace(tmp, file)
    except OSError as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise Invalid(f"the deployment details can't be saved in {file} ({e})") from None


def _set_times(config):
    try:
        with open(path(config), encoding='utf-8') as f:
            times = json.load(f).get('set')
        return dict(times) if isinstance(times, dict) else {}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def describe(name, value):
    if name == 'clock_offset':
        return f'{value:+d} s'
    return value


def settle(config, save):
    """The config with its deployment details worked out: the options given,
    otherwise what deployment.json holds. With save, options that change what's
    saved are saved. Says what's in use; Invalid if it can't be used. Settling
    a settled config returns it unchanged, so a run says it once."""
    if config.deployment_settled:
        return config
    if config.timezone is not None:
        zone(config.timezone)   # an unknown name stops the run before anything is written
    saved = read(config)
    given = {name: getattr(config, name) for name in FIELDS if getattr(config, name) is not None}
    values = {**saved, **given}
    changed = {name: value for name, value in given.items() if saved.get(name) != value}
    if save and changed:
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        times = _set_times(config)
        times.update({name: now for name in changed})
        write(config, values, times)
        for name, value in changed.items():
            if name in saved:
                rest = ('; days are regrouped, and merged files for days that no longer have events are '
                        'reported' if name in ('timezone', 'clock_offset') else '')
                log.say(_log, f"WARNING: the {LABELS[name]} changed from {describe(name, saved[name])} to "
                              f"{describe(name, value)} (saved in {path(config)}){rest}")
        new = [name for name in changed if name not in saved]
        if new:
            log.say(_log, f"Saved the {', '.join(LABELS[name] for name in new)} in {path(config)}")

    def source(name):
        if name in given:
            return 'option' if save or saved.get(name) == given[name] else 'option, not saved'
        return FILE_NAME
    if 'timezone' in values:
        log.say(_log, f"Time zone: {values['timezone']} ({source('timezone')})")
    else:
        log.say(_log, f"WARNING: no time zone set: days are grouped in this computer's ({computer_zone()}). "
                      f"Give the camera's with --timezone (such as --timezone Europe/Vilnius); it's saved "
                      f"for later runs")
    if values.get('clock_offset'):
        log.say(_log, f"Clock offset: {describe('clock_offset', values['clock_offset'])} ({source('clock_offset')})")
    settled = {name: values.get(name) for name in FIELDS}
    # The run log's first record has the settings as given; this has them as used
    log.file_only(_log, logging.INFO, 'Deployment details in use', settings=settled)
    return replace(config, **settled, deployment_settled=True)


def computer_zone():
    """The computer's time zone, by the name it gives (which may not be an IANA one)."""
    return datetime.now().astimezone().tzname()


def summary(config):
    """What a settled config's time zone and clock offset are, for a run's summary."""
    zone = config.timezone or f"none set, so this computer's ({computer_zone()})"
    offset = f"; clock offset {describe('clock_offset', config.clock_offset)}" if config.clock_offset else ''
    return f"Time zone: {zone}{offset}"
