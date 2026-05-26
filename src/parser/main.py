import sys, pathlib
from chatParse import ChatParser

def main():
    if len(sys.argv) != 3:
        print("Usage: python main.py <input.txt> <output.json>")
        sys.exit(1)

    input_file = pathlib.Path(sys.argv[1])
    output_file = pathlib.Path(sys.argv[2])

    parser = ChatParser()
    parser.parse_chat_file(input_file)
    parser.exportJson(output_file)

if __name__ == "__main__":
    main()