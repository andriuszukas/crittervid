"""Deployment details: the camera's time zone and clock offset, the site and the
camera (decision record 0005), and the real starts of series of events recorded
before the camera's clock was set (decision record 0006).

They're saved in <output>/deployment.json the first time they're given, and
read from there on later runs, so every step on an output folder groups days
the same way. An option given on a later run replaces the saved value.
"""
import argparse
import json
import logging
import os
import re
import time
from dataclasses import replace
from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

from . import log, media_format

FILE_NAME = 'deployment.json'
SCHEMA = 2          # the newest this version reads; written only for files with series
PLAIN_SCHEMA = 1    # for files without series, which versions before 0006 read too
SERIES_NAME = re.compile(r'\d{10,}_\d+')   # a series is named after its first event
FIELDS = ('timezone', 'clock_offset', 'site', 'camera')
MAX_CLOCK_OFFSET = 10 * 366 * 86400   # seconds: a clock more than 10 years out wasn't set (#73)
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
    parser.add_argument('--series-start', type=series_start, action='append', default=None, metavar='NAME=TIME',
                        help="The real start of the first event of a series recorded before the camera's clock "
                             "was set, such as 0000000001_0602=2025-06-01T14:30 (the camera's local time, or "
                             "give a UTC offset); the merge lists the series. Can be given for each series")


def series_start(text):
    """NAME=TIME from the command line, as (name, time)."""
    name, sep, when = text.partition('=')
    if not sep or not name.strip() or not when.strip():
        raise argparse.ArgumentTypeError(f"expected NAME=TIME, such as 0000000001_0602=2025-06-01T14:30, not '{text}'")
    return name.strip(), when.strip()


def options(config):
    """The deployment options doing what the config says."""
    args = []
    for name in FIELDS:
        value = getattr(config, name)
        if value is not None:
            args.append(f"--{name.replace('_', '-')}={value}")
    for name, when in config.series or ():
        args.append(f"--series-start={name}={when}")
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


def check_offset(seconds, where=''):
    """Invalid for a clock offset too big to be a wrong clock."""
    if abs(seconds) > MAX_CLOCK_OFFSET:
        raise Invalid(f"{where}a clock offset of {seconds} s is more than 10 years; a camera whose clock "
                      f"wasn't set is handled separately")


def resolve_start(name, when, timezone_name):
    """A series start as given (an ISO 8601 date and time, the camera's local time
    without a UTC offset), as an ISO time with its UTC offset; Invalid if the name
    or the time can't be used."""
    if not SERIES_NAME.fullmatch(name) or media_format.clock_was_set(int(name.split('_')[0])):
        raise Invalid(f"'{name}' isn't the name of a series of events recorded before the camera's clock was "
                      f"set (a name like 0000000001_0602; the merge lists them)")
    try:
        start = datetime.fromisoformat(when[:-1] + '+00:00' if when[-1:] in 'Zz' else when)
    except ValueError:
        raise Invalid(f"series {name}: '{when}' isn't a date and time such as 2025-06-01T14:30") from None
    if start.tzinfo is None:
        if not timezone_name:
            raise Invalid(f"series {name}: {when} has no UTC offset, so it's the camera's local time, but no time "
                          f"zone is set; give --timezone, or a time with its offset such as {when}+02:00")
        tz = zone(timezone_name)
        aware = start.replace(tzinfo=tz)   # fold 0: the first, when the time happens twice
        if datetime.fromtimestamp(aware.timestamp(), tz).replace(tzinfo=None) != start:
            raise Invalid(f"series {name}: {when} doesn't exist in {timezone_name}, because the clocks went "
                          f"forward then")
        start = aware
    if not media_format.clock_was_set(int(start.timestamp())) or start.timestamp() > time.time():
        raise Invalid(f"series {name}: {when} can't be when it started: it's in the future or before 2000")
    return start.isoformat()


def series_starts(config):
    """{series name: its first event's real start, in Unix seconds} for a settled config."""
    return {name: int(datetime.fromisoformat(when).timestamp()) for name, when in config.series or ()}


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
            check_offset(value, f"{file}: ")
        elif not isinstance(value, str):
            raise Invalid(f"{file}: the {LABELS[name]} must be text, not {value!r}")
        if name == 'timezone':
            try:
                zone(value)
            except Invalid as e:
                raise Invalid(f"{file}: {e}") from None
        saved[name] = value
    series = data.get('series')
    if series is not None:
        if not isinstance(series, dict):
            raise Invalid(f"{file}: series must be a map of series names to times, not {series!r}")
        for name, when in series.items():
            if not isinstance(when, str):
                raise Invalid(f"{file}: series {name}: the start must be a time, not {when!r}")
            try:
                if datetime.fromisoformat(when).tzinfo is None:
                    raise ValueError
                resolve_start(name, when, None)
            except ValueError:
                raise Invalid(f"{file}: series {name}: '{when}' isn't a date and time with its UTC offset") from None
            except Invalid as e:
                raise Invalid(f"{file}: {e}") from None
        if series:
            saved['series'] = dict(series)
    return saved


def write(config, values, set_times):
    """Save values to deployment.json, through a temporary file renamed into place,
    so a run stopped halfway leaves the old file."""
    file = path(config)
    data = {'schema': SCHEMA if values.get('series') else PLAIN_SCHEMA,
            **{name: values.get(name) for name in FIELDS}}
    if values.get('series'):
        data['series'] = dict(sorted(values['series'].items()))
    data['set'] = set_times
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
    if config.clock_offset is not None:
        check_offset(config.clock_offset)
    saved = read(config)
    given = {name: getattr(config, name) for name in FIELDS if getattr(config, name) is not None}
    values = {**saved, **given}
    changed = {name: value for name, value in given.items() if saved.get(name) != value}
    # Series starts: resolved in the time zone in use, saved with their UTC offsets
    saved_series = saved.get('series', {})
    given_series = {name: resolve_start(name, when, values.get('timezone')) for name, when in config.series or ()}
    values['series'] = {**saved_series, **given_series}
    changed_series = {name: when for name, when in given_series.items() if saved_series.get(name) != when}
    if save and (changed or changed_series):
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        times = _set_times(config)
        times.update({name: now for name in changed})
        times.update({f'series {name}': now for name in changed_series})
        write(config, values, times)
        for name, when in changed_series.items():
            if name in saved_series:
                log.say(_log, f"WARNING: the start of series {name} changed from {saved_series[name]} to {when} "
                              f"(saved in {path(config)}); its events are regrouped")
            else:
                log.say(_log, f"Saved the start of series {name} in {path(config)}")
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
    for name, when in sorted(values['series'].items()):
        where = (FILE_NAME if name not in given_series else
                 'option' if save or saved_series.get(name) == when else 'option, not saved')
        log.say(_log, f"Series {name} started at {when} ({where})")
    settled = {name: values.get(name) for name in FIELDS}
    settled['series'] = tuple(sorted(values['series'].items()))
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
