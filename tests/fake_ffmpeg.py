"""
Stand-in for ffmpeg used by the tests. It understands just enough of the
command lines the pipeline builds to simulate success and failure:

- Convert: output is b'OK' + input. Input containing b'BAD' writes a partial
  output file and exits 1, like an encode that dies halfway.
- Concat (merge): output is b'OK' + all listed files. Fails like convert if any
  listed file contains b'BAD'.
- Decode to null (validate): exits 0 only if the file starts with b'OK'.

If FAKE_FFMPEG_LOG is set, each command line is appended to that file as JSON.
"""
import json
import os
import sys


def parse_concat_line(line):
    """Read a `file <path>` line of a concat list the way FFmpeg does.

    The list is UTF-8. Inside single quotes everything is literal up to the next
    quote; outside quotes a backslash escapes the next character, and whitespace
    ends the path. So a quote within a quoted path must be written as '\\'' and an
    unescaped one leaves the path wrong or a quote unclosed, which is an error.
    """
    text = line.strip()
    if not text.startswith('file '):
        sys.exit(f'fake ffmpeg: bad concat line {line!r}')
    rest = text[len('file '):].lstrip()
    path, quoted, i = [], False, 0
    while i < len(rest):
        c = rest[i]
        if quoted:
            if c == "'":
                quoted = False
            else:
                path.append(c)
        elif c == "'":
            quoted = True
        elif c == '\\' and i + 1 < len(rest):
            i += 1
            path.append(rest[i])
        elif c.isspace():
            break
        else:
            path.append(c)
        i += 1
    if quoted or rest[i:].strip():
        sys.exit(f'fake ffmpeg: bad concat line {line!r}')
    return ''.join(path)


def fail(out_path):
    with open(out_path, 'wb') as f:
        f.write(b'PARTIAL')
    print('fake ffmpeg: simulated failure', file=sys.stderr)
    sys.exit(1)


def main():
    args = sys.argv[1:]
    if os.environ.get('FAKE_FFMPEG_LOG'):
        with open(os.environ['FAKE_FFMPEG_LOG'], 'a', encoding='utf-8') as log:
            log.write(json.dumps(args) + '\n')
    out_path = args[-1]
    in_path = args[args.index('-i') + 1]

    if out_path == '-':
        with open(in_path, 'rb') as f:
            sys.exit(0 if f.read().startswith(b'OK') else 1)

    if '-f' in args and args[args.index('-f') + 1] == 'concat':
        data = b''
        with open(in_path, encoding='utf-8') as filelist:
            for line in filelist:
                # Directives such as `inpoint 0` apply to the file before them
                if line.split(' ', 1)[0] in ('inpoint', 'outpoint', 'duration'):
                    continue
                with open(parse_concat_line(line), 'rb') as f:
                    data += f.read()
    else:
        with open(in_path, 'rb') as f:
            data = f.read()

    if b'BAD' in data:
        fail(out_path)
    with open(out_path, 'wb') as f:
        f.write(b'OK' + data)


if __name__ == '__main__':
    main()
