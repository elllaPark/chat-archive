import argparse
import pathlib
from chatParse import ChatParser

def main():
    """CLI entry point used by Electron and manual parser checks."""

    arg_parser = argparse.ArgumentParser(description="Parse a KakaoTalk export file.")
    arg_parser.add_argument("input_file")
    arg_parser.add_argument("output_file")
    arg_parser.add_argument(
        "--include-metadata",
        action="store_true",
        help="Reserved for future parser metadata export; default output stays renderer-compatible.",
    )
    args = arg_parser.parse_args()

    input_file = pathlib.Path(args.input_file)
    output_file = pathlib.Path(args.output_file)

    parser = ChatParser()
    parser.parse_chat_file(input_file)
    # Keep default output stable for the renderer; metadata is available on demand.
    if args.include_metadata:
        parser.exportResultJson(output_file)
    else:
        parser.exportJson(output_file)

if __name__ == "__main__":
    main()
