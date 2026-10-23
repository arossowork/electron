# Framework self-tests on the host (no board).
CC ?= gcc
CFLAGS := -std=c11 -Wall -Wextra -Werror -O1 -Iinclude -Itest
OUT ?= /tmp/fwk_tests

.PHONY: test
test:
	$(CC) $(CFLAGS) -o $(OUT) src/fwk.c test/fwk_sim.c test/test_fwk.c
	$(OUT)
