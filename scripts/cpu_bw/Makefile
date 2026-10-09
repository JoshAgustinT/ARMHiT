CXX ?= g++
CXXFLAGS ?= -std=c++20 -O3 -mcpu=native -pthread

mem: mem.cpp
	$(CXX) $(CXXFLAGS) mem.cpp -o mem

clean:
	rm -f mem

.PHONY: clean
