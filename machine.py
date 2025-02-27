import logging
import sys
from typing import List, Final

from isa import Opcode, read_code
from translator import is_number

MIN_: Final = -pow(2, 31)
MAX_: Final = pow(2, 31) - 1


class Memory:
    """
    Command/Data Memory
    Addresses:
        0 - input_hello port
        1 - output port
        2 - 127 - data memory
        128 - memory_size - program memory
    """

    def __init__(self, memory_size, program, input_buffer, data):
        assert memory_size > 0, \
            "Memory size should be more then zero"
        assert memory_size > len(program) + 128, \
            "Memory size should be more then program size"
        self.data_address = 0
        self.memory_size = memory_size
        self.data: int | dict = data + [0] * (memory_size - len(program) - len(data)) + program
        self.program_start = memory_size - len(program)
        self.program_len = len(program)
        self.input_buffer = input_buffer
        self.output_buffer = []

    def latch_data_address(self, val):
        assert 0 <= val <= self.memory_size, \
            f'data_address must be in [0;{self.memory_size}]'
        self.data_address = val

    def read(self):
        assert self.data_address != 1, \
            "Trying to read from output device"
        if self.data_address != 0:
            return self.data[self.data_address]

        if len(self.input_buffer) == 0:
            logging.debug('Input buffer is Empty')
            raise StopIteration()

        value_as_string = self.input_buffer.pop(0)
        value = ord(value_as_string[0])
        self.data[0] = value
        logging.info('input: %s << %s', ','.join(self.input_buffer), value_as_string)
        return value

    def write(self, value):
        assert self.data_address != 0, \
            "Trying to write to input_hello device"

        if self.data_address != 1:
            self.data[self.data_address] = value
            return

        self.data[1] = value
        if 0 <= value <= 127:
            value = chr(value)

        value = str(value)
        logging.debug('output: %s << %s', ','.join(self.output_buffer), value)
        self.output_buffer.append(value)

    def get_instruction(self, pc):
        assert pc < self.program_len, \
            "The interrupt was caught, but interrupt code wasn't found"

        pc += self.program_start
        assert 128 <= pc <= self.memory_size, \
            "PC is out of program memory"
        return self.data[pc]


class DataPath:
    def __init__(self, memory_size: int, memory: Memory):
        assert memory_size > 0, \
            "Memory size should be more then zero"
        self.stack: List[int] = [0] * memory_size
        self.head: int = 0
        self.memory_size: int = memory_size
        self.memory = memory
        self.alu = 0

    def get_tos(self, offset):
        assert 0 <= offset <= 3, \
            "You have access only to the first 3 elements from top"
        assert offset < self.head, \
            "Trying to get non-existent element"
        return self.stack[self.head - offset - 1]

    def latch_head(self, sel_val):
        assert sel_val in {-1, -3, 1, -2}, \
            "You can't change the head value in increments of 0"
        self.head += sel_val
        assert 0 <= self.head < self.memory_size, \
            f"out of memory: {self.head}"

    def push(self, val):
        self.stack[self.head] = val
        self.latch_head(1)

    def oe_stack(self, offset, selector=None):
        assert self.head >= 1, \
            "End of stack"
        val = self.get_tos(offset)
        if selector in {Opcode.READ_NDR,
                        Opcode.WR_NDR}:
            val += 1
        # logging.info('output: %s << %s', ','.join(self.memory.output_buffer), val)
        return val

    def is_true(self):
        res = (0, -1)[self.get_tos(0) == -1]
        self.latch_head(-1)
        return res

    def latch_alu(self, op_sel):
        if op_sel == Opcode.READ_NDR:
            self.alu = int(self.get_tos(1)) + 1

        if op_sel == Opcode.MOD.value:
            self.alu = int(self.get_tos(1)) % int(self.get_tos(0))

        if op_sel == Opcode.PLUS.value:
            self.alu = int(self.get_tos(1)) + int(self.get_tos(0))

        if op_sel == Opcode.MULT.value:
            self.alu = int(self.get_tos(1)) * int(self.get_tos(0))

        if op_sel == Opcode.POW.value:
            self.alu = int(self.get_tos(0)) ** int(self.get_tos(1))

        if op_sel == Opcode.LT.value:
            self.alu = (0, -1)[int(self.get_tos(1)) < int(self.get_tos(0))]

        if op_sel == Opcode.NEG.value:
            self.alu = int(self.get_tos(0)) * (-1)

        if op_sel == Opcode.INV.value:
            self.alu = (int(self.get_tos(0)) + 1) * (-1)

        if op_sel == Opcode.JNT.value:
            self.alu = (0, -1)[int(self.get_tos(0)) == -1]


class ControlUnit:

    def __init__(self, data_path: DataPath, memory: Memory):
        self.data_path = data_path
        self.mem = memory
        self.pc = 0
        self._tick = 0

    def tick(self):
        self._tick += 1
        logging.debug('%s', self)

    def cur_tick(self):
        return self._tick

    def latch_pc(self, sel_next):
        if sel_next:
            self.pc += 1
        else:
            instr = self.mem.get_instruction(self.pc)
            assert instr["opcode"].value in {
                Opcode.JMP.value,
                Opcode.JNT.value
            }, f"instruction must has an argument: {instr}"
            assert 'arg' in instr, "internal error"
            self.pc = instr["arg"]

    def execute_micro_operation(self, instr):
        opcode = instr["opcode"]
        handlers = {
            Opcode.READ_DIR: self.micro_read_direct,
            Opcode.READ_NDR: self.micro_read_indirect,
            Opcode.WR_DIR: self.micro_write_direct,
            Opcode.WR_NDR: self.micro_write_indirect,
            Opcode.JNT: self.micro_jump_if_not_true,
            Opcode.HALT: self.micro_halt,
            Opcode.BEGIN: self.micro_no_operation,
            Opcode.NOP: self.micro_no_operation,
            Opcode.MOD: self.micro_alu_operation,
            Opcode.PLUS: self.micro_alu_operation,
            Opcode.MULT: self.micro_alu_operation,
            Opcode.LT: self.micro_alu_operation,
            Opcode.DIV: self.micro_alu_operation,
            Opcode.POW: self.micro_alu_operation,
            Opcode.NEG: self.micro_alu_operation,
            Opcode.INV: self.micro_alu_operation,
            Opcode.ROT: self.micro_rotate,
            Opcode.DUP: self.micro_duplicate,
            Opcode.OVER: self.micro_over,
            Opcode.SWAP: self.micro_swap,
            Opcode.PUSH: self.micro_push,
            Opcode.DROP: self.micro_drop,
            Opcode.JMP: self.micro_jump
        }
        handler = handlers.get(opcode)
        if handler:
            handler(instr)

    def micro_read_direct(self, instr):
        top = self.data_path.oe_stack(0)
        self.mem.latch_data_address(top)
        self.data_path.latch_head(-1)
        self.tick()

        val = self.mem.read()
        self.data_path.push(val)
        self.latch_pc(sel_next=True)
        self.tick()
        pass

    def micro_read_indirect(self, instr):
        address = self.data_path.oe_stack(0)
        self.mem.latch_data_address(address)
        self.tick()

        val = self.mem.read()
        self.data_path.push(val)
        self.tick()

        address = self.data_path.oe_stack(0)
        self.mem.latch_data_address(address)
        self.tick()

        val = self.mem.read()
        self.data_path.push(val)
        self.tick()

        v1 = self.data_path.get_tos(0)
        v2 = self.data_path.get_tos(1)
        v3 = self.data_path.get_tos(2)
        self.data_path.latch_head(-3)
        self.data_path.push(v1)
        self.data_path.push(v2)
        self.data_path.push(v3)
        self.tick()

        address = self.data_path.oe_stack(0)
        self.mem.latch_data_address(address)
        self.tick()

        val = self.data_path.oe_stack(1, instr["opcode"])
        self.mem.write(val)
        self.data_path.latch_head(-2)
        self.latch_pc(sel_next=True)
        self.tick()
        pass

    def micro_write_direct(self, instr):
        address = self.data_path.get_tos(0)
        self.mem.latch_data_address(address)
        self.tick()

        val = self.data_path.oe_stack(1)
        self.mem.write(val)
        self.data_path.latch_head(-2)
        self.latch_pc(sel_next=True)
        self.tick()
        pass

    def micro_write_indirect(self, instr):
        address = self.data_path.oe_stack(0)
        self.mem.latch_data_address(address)
        self.tick()

        val = self.mem.read()
        self.data_path.push(val)
        self.tick()

        address = self.data_path.oe_stack(0)
        self.mem.latch_data_address(address)
        self.tick()

        val = self.data_path.oe_stack(2)
        self.mem.write(val)
        self.tick()

        address = self.data_path.oe_stack(1)
        self.mem.latch_data_address(address)
        self.tick()

        val = self.data_path.oe_stack(0, instr["opcode"])
        self.mem.write(val)
        self.data_path.latch_head(-3)
        self.latch_pc(sel_next=True)
        self.tick()
        pass

    def micro_jump_if_not_true(self, instr):
        if self.data_path.is_true():
            self.latch_pc(sel_next=True)
        else:
            self.latch_pc(sel_next=False)
        self.tick()
        pass

    def micro_halt(self, instr):
        raise StopIteration()

    def micro_no_operation(self, instr):
        self.latch_pc(sel_next=True)
        self.tick()

    def micro_alu_operation(self, instr):
        self.data_path.latch_alu(instr["opcode"])
        if instr["opcode"] in {Opcode.NEG.value,
                      Opcode.INV.value}:
            self.data_path.latch_head(-1)
        else:
            self.data_path.latch_head(-2)
        self.tick()

        self.data_path.push(self.data_path.alu)
        self.latch_pc(sel_next=True)
        self.tick()
        pass

    def micro_rotate(self, instr):
        v1 = self.data_path.get_tos(1)
        v2 = self.data_path.get_tos(0)
        v3 = self.data_path.get_tos(2)

        self.data_path.latch_head(-3)
        self.data_path.push(v1)
        self.data_path.push(v2)
        self.data_path.push(v3)

        self.latch_pc(sel_next=True)
        self.tick()
        pass

    def micro_duplicate(self, instr):
        v = self.data_path.get_tos(0)
        self.data_path.push(v)
        self.latch_pc(sel_next=True)
        self.tick()
        pass

    def micro_over(self, instr):
        v = self.data_path.get_tos(1)
        self.data_path.push(v)
        self.latch_pc(sel_next=True)
        self.tick()
        pass

    def micro_swap(self, instr):
        v1 = self.data_path.get_tos(0)
        v2 = self.data_path.get_tos(1)
        self.data_path.latch_head(-2)
        self.data_path.push(v1)
        self.data_path.push(v2)
        self.latch_pc(sel_next=True)
        self.tick()
        pass

    def micro_push(self, instr):
        if is_number(instr["arg"]):
            value = int(instr["arg"])
        else:
            value = ord(instr["arg"])
        self.data_path.push(value)
        self.latch_pc(sel_next=True)
        self.tick()
        pass

    def micro_drop(self, instr):
        self.data_path.latch_head(-1)
        self.latch_pc(sel_next=True)
        self.tick()
        pass

    def micro_jump(self, instr):
        addr = instr["arg"]
        self.pc = addr
        self.tick()
        pass

    def decode_and_execute(self):
        instr = self.mem.get_instruction(self.pc)
        self.execute_micro_operation(instr)

    def __repr__(self):
        state = f"{{TICK: {self._tick}, " \
                f"PC: {self.pc}, " \
                f"HEAD: {self.data_path.head}, " \
                f"TOS: {self.data_path.stack[self.data_path.head - 3]}, " \
                f"{self.data_path.stack[self.data_path.head - 2]}, {self.data_path.stack[self.data_path.head - 1]}}} "

        instr = self.mem.get_instruction(self.pc)
        opcode = instr["opcode"]
        arg = instr.get("arg", "")
        term = instr["term"]
        action = f"{opcode.__class__.__name__}.{opcode.name} {arg} ('{term.arg}' @ {term.line_num}:{term.com})"
        return f"{state} {action}"


def simulation(code, data_memory_size, limit, input_buffer, data):
    memory = Memory(data_memory_size, code, input_buffer, data)
    data_path = DataPath(256, memory)
    control_unit = ControlUnit(data_path, memory)

    instruct_counter = 0

    try:
        while True:
            if limit <= instruct_counter:
                logging.error("Too long execution!")
                break

            control_unit.decode_and_execute()
            instruct_counter += 1
    except StopIteration:
        pass
    return memory.output_buffer, instruct_counter, control_unit.cur_tick()


def main(args):
    if len(args) == 2:
        code_file, input_file = args
    else:
        code_file = args[0]
        input_file = ""

    code, data = read_code(code_file)
    if input_file != "":
        with open(input_file, encoding="utf-8") as file:
            input_text = file.read()
            input_list = []
            for ch in input_text:
                input_list.append(ch)
    else:
        input_list = []

    output, instr_counter, ticks = simulation(code, 600, 3000, input_list, data)
    print("output:", ''.join(output))
    print("instr_counter: ", instr_counter, "ticks:", ticks)
    return output


if __name__ == '__main__':
    logging.getLogger().setLevel(logging.DEBUG)
    main(sys.argv[1:])