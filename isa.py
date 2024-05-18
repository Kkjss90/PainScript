import json
from collections import namedtuple
from enum import Enum


class Opcode(str, Enum):
    WR_DIR = 'WRITE_DIR'
    WR_NDR = 'WRITE_NDR'
    READ_DIR = 'READ_DIR'
    READ_NDR = 'READ_NDR'

    PRINT = 'PRINT'

    BEGIN = 'BEGIN'
    NOP = 'NOP'

    MOD = 'MOD'
    PLUS = 'PLUS'
    MINUS = 'MINUS'
    MULT = 'MULT'
    DIV = 'DIV'
    POW = 'POW'
    LT = 'LT'
    NEG = 'NEG'
    INV = 'INVERT'

    DUP = 'DUP'
    OVER = 'OVER'
    ROT = 'ROT'
    SWAP = 'SWAP'

    PUSH = 'PUSH'
    DROP = 'DROP'

    JMP = 'JMP'
    JNT = 'JNT'

    HALT = 'HALT'


class Term(namedtuple('Term', 'line_num com arg')):
    """Описание выражения из исходного текста программы."""


def write_code(filename, code, data):
    """Записать машинный код в файл."""
    with open(filename, "w", encoding="utf-8") as file:
        if len(data) != 0:
            file.write("data: ")
            file.write(json.dumps(data, indent=4))
            file.write("\n")
        file.write(json.dumps(code, indent=4))


def read_code(filename):
    """Прочесть машинный код из файла."""
    with open(filename, encoding="utf-8") as file:
        full = file.read()
        if "data" in full:
            split_content = full.split(']\n[')
            data_part = split_content[0].replace("data: ", "") + ']'
            code_part = '[' + split_content[1]

            data = json.loads(data_part)
            code = json.loads(code_part)
        else:
            code = json.loads(file.read())
    for instr in code:
        instr['opcode'] = Opcode(instr['opcode'])
        if 'term' in instr:
            instr['term'] = Term(
                instr['term'][0], instr['term'][1], instr['term'][2])
    return code, data
