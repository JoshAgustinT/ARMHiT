NVCC ?= /usr/local/cuda/bin/nvcc
NVFLAGS ?= -O3 -arch=sm_110

all: hello mem

hello: hello.cu
	$(NVCC) $(NVFLAGS) hello.cu -o hello

mem: mem.cu
	$(NVCC) $(NVFLAGS) mem.cu -o mem

run: hello
	./hello

clean:
	rm -f hello mem

.PHONY: all run clean
