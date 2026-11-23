"""Manual managed-import entry point; Electron integration is a later milestone."""

import argparse
import json

from .managed import import_managed_archive, inspect_archive


def main():
    parser = argparse.ArgumentParser(description='Import or inspect a local chat archive')
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('import')
    create.add_argument('source')
    create.add_argument('destination')
    resume = commands.add_parser('resume')
    resume.add_argument('destination')
    inspect = commands.add_parser('inspect')
    inspect.add_argument('destination')
    inspect.add_argument('--verify-files', action='store_true')
    args = parser.parse_args()
    if args.command == 'inspect':
        result = inspect_archive(args.destination, verify_files=args.verify_files)
    else:
        result = import_managed_archive(args.source if args.command == 'import' else None,
                                       args.destination)
    # Summary only; no message bodies, senders, or private filenames on stdout.
    print(json.dumps({key: result[key] for key in ('archiveId', 'messageCount', 'assetCount', 'fileCount')}, indent=2))


if __name__ == '__main__':
    main()
