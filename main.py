from __future__ import annotations

import abc
import operator
import cmd
import contextlib
import dataclasses
import argparse
import json
import shlex
import codecs
import functools
import collections
import textwrap


myOpen = functools.partial(codecs.open, encoding="utf-8-sig")


@dataclasses.dataclass
class JsonObject:
    _inner: dict = dataclasses.field(init=False)
    json_val: dataclasses.InitVar[dict]

    def __post_init__(self, json_val: dict):
        self._inner = json_val

    def get(self, val):
        return self._inner[val]

    def set(self, key, val):
        self._inner[key] = val


class ChatExport(JsonObject):
    def __post_init__(self, json_val):
        super().__post_init__(json_val=json_val)

        for idx, msg in enumerate(self.messages):
            new_msg = MsgFactory.get_message(msg)
            self.messages[idx] = new_msg

    @classmethod
    def hook(cls, json_obj):
        return cls(json_val=json_obj)

    @property
    def name(self):
        return self.get("name")

    @property
    def id(self):
        return self.get("id")

    @property
    def messages(self) -> list[Message]:
        return self.get("messages")


class Message(JsonObject):
    @dataclasses.dataclass
    class Text:
        contents: list = dataclasses.field(init=False)
        raw_contents: dataclasses.InitVar[str | list]

        def __repr__(self):
            res = []
            for el in self.contents:
                if not isinstance(el, str):
                    try:
                        res.append(el["text"])
                    except KeyError:
                        res.append(f"<{el['type']}>")
                    continue

                res.append(el)

            return "".join(res)

        def __post_init__(self, raw_contents: str | list):
            if isinstance(raw_contents, str):
                self.contents = [raw_contents]
            else:
                self.contents = raw_contents

        def count(self, word: str, case_sensitive=False) -> int:
            counts = []
            for s in self.contents:
                if not isinstance(s, str):
                    try:
                        s = s["text"]
                    except KeyError:
                        continue

                if not case_sensitive:
                    s = s.lower()
                    word = word.lower()
                counts.append(s.count(word))

            res = sum(counts)
            return res

    def __post_init__(self, json_val):
        super().__post_init__(json_val=json_val)

        text = self.Text(raw_contents=self.text)
        self.text = text

    @property
    def id(self):
        return self.get("id")

    @property
    def type(self):
        return self.get("type")

    @property
    def date_unixtime(self):
        return self.get("date_unixtime")

    @property
    def date(self):
        return self.get("date")

    @property
    def text(self):
        return self.get("text")

    @text.setter
    def text(self, val):
        self.set("text", val)


class RegularMessage(Message):
    @property
    def from_usr(self):
        return self.get("from")


class ServiceMessage(Message):
    @property
    def action(self):
        return self.get("action")


class MsgFactory:
    MAPPING = {"service": ServiceMessage, "message": RegularMessage}

    @classmethod
    def get_message(cls, json_obj: dict) -> Message:

        msg_type = json_obj["type"]
        if msg_type in cls.MAPPING:
            return cls.MAPPING[msg_type](json_val=json_obj)
        else:
            raise ValueError(f"Unhandled message type: {msg_type}")


class Command(abc.ABC):
    def __init__(self, arg: str):
        self.parser = self.arg_parser()
        args = shlex.split(arg)
        self.args = self.parser.parse_args(args)

    @classmethod
    @abc.abstractmethod
    def run(cls, app: App):
        """
        Performs what the command is supposed to do.
        All commands have read access to the app
        """
        ...

    @classmethod
    @abc.abstractmethod
    def arg_parser(cls) -> argparse.ArgumentParser:
        """
        Initializes the parser of this command's arguments
        """
        ...


class App(cmd.Cmd):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.parser = argparse.ArgumentParser(prog="TgAnalyze")
        self.parser.add_argument("--input-file", "-i", required=True)
        self.args = self.parser.parse_args()

        self.chat = self.load_file(self.args.input_file)

    class _WordCount(Command):
        @classmethod
        def arg_parser(_cls) -> argparse.ArgumentParser:
            parser = argparse.ArgumentParser()
            parser.add_argument("words", nargs="+")
            parser.add_argument("--case-sensitive", "-c", action="store_true")
            parser.add_argument("--per-user", "-u", action="store_true")
            return parser

        def run(self, app: App):
            words = self.args.words

            if not self.args.per_user:
                count = collections.Counter()
                for msg in app.chat.messages:
                    for word in words:
                        count[word] += msg.text.count(word, self.args.case_sensitive)

                for item, count in count.items():
                    print(f"{item}: {count}")
                return

            user_count = collections.defaultdict(collections.Counter)
            for msg in app.chat.messages:
                for word in words:
                    count = msg.text.count(word, self.args.case_sensitive)
                    if count:
                        user_count[msg.from_usr][word] += count
            for user, word_counts in user_count.items():
                print(user)
                res = [
                    f"{word}: {count}" for word, count in sorted(word_counts.items())
                ]
                res = "\n".join(res)
                res = textwrap.indent(res, "- ")
                print(res)

    class _WordGrep(Command):
        @classmethod
        def arg_parser(_cls) -> argparse.ArgumentParser:
            parser = argparse.ArgumentParser()
            parser.add_argument("words", nargs="+")
            parser.add_argument("--case-sensitive", "-c", action="store_true")
            return parser

        def run(self, app: App):
            count = collections.Counter()
            words = self.args.words
            for msg in app.chat.messages:
                for word in words:
                    count = msg.text.count(word, self.args.case_sensitive)
                    if count:
                        print(f"{word}: [{msg.from_usr}] {msg.text}")

    class _MsgCount(Command):
        @classmethod
        def arg_parser(_cls) -> argparse.ArgumentParser:
            parser = argparse.ArgumentParser()
            parser.add_argument("--per-user", "-u", action="store_true")
            return parser

        def run(self, app: App):
            if not self.args.per_user:
                print(len(app.chat.messages))
                return

            count = collections.Counter()

            for msg in app.chat.messages:
                # If there's no such key -- just ignore it and skip
                with contextlib.suppress(AttributeError):
                    count[msg.from_usr] += 1

            for usr, msgs in sorted(count.items(), key=operator.itemgetter(1)):
                print(f"{usr}: {msgs}")

    @staticmethod
    def load_file(file: str) -> ChatExport:
        with myOpen(file, "r") as f:
            chat = json.load(f)
        chat = ChatExport(json_val=chat)

        return chat

    def do_wcount(self, arg):
        """
        Count how much a certain string or strings
        have been encountered in the chat history

        [--case-sensitive/-c]: Case sensitive search
        """
        self._WordCount(arg).run(self)

    def do_msgcount(self, arg):
        """
        How many messages were sent in every chat

        [--per-user/-u]: Print stats for every user
        """
        self._MsgCount(arg).run(self)

    def do_wgrep(self, arg):
        """
        Find messages that contain a certain word

        Args same as wcount
        """
        self._WordGrep(arg).run(self)

    def do_q(self, _arg):
        """Quit the program"""
        return True


def main():
    App().cmdloop()


if __name__ == "__main__":
    main()
