// Command line parsing. Option names and defaults follow DRAMHiT's
// Application.cpp (boost::program_options): "--name value" or "--name=value",
// and, like boost, a unique prefix of a name is accepted (e.g. --find_queue).
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "dramhit.hpp"

Configuration config;

enum OptType { U32, U64, I64, DBL, BOOL, HELP };

struct Option {
  const char *name;
  OptType type;
  void *dst;
  const char *help;
};

static const Option options[] = {
    {"help", HELP, nullptr, "produce help message"},
    {"mode", U32, &config.mode, "11: uniform test (the only supported mode)"},
    {"ht-type", U32, &config.ht_type, "3: casht++ (the only supported type)"},
    {"num-threads", U32, &config.num_threads, "number of threads"},
    {"numa-split", U32, &config.numa_split, "numa policy (ignored: one node)"},
    {"ht-fill", U32, &config.ht_fill, "hashtable fill ratio [1-99]"},
    {"ht-size", U64, &config.ht_size, "hashtable size in slots (rounded to pow2)"},
    {"insert-factor", U64, &config.insert_factor, "repeat insert workload X times"},
    {"read-factor", U64, &config.read_factor, "repeat read workload X times"},
    {"batch-len", U32, &config.batch_len, "batch length"},
    {"find_queue_sz", U32, &config.find_queue_sz, "find/insert queue size (pow2)"},
    {"skew", DBL, &config.skew, "(ignored: no zipfian)"},
    {"seed", I64, &config.seed, "(ignored: index workload)"},
    {"hw-pref", BOOL, &config.hw_pref, "(ignored: no MSRs on ARM)"},
    {"no-prefetch", BOOL, &config.no_prefetch, "(ignored)"},
    {"drop-caches", BOOL, &config.drop_caches, "(ignored)"},
    {"test", BOOL, &config.test, "check fill and found counts"},
};

[[noreturn]] static void die(const std::string &msg) {
  PLOGE("%s", msg.c_str());
  exit(-1);
}

// Exact match, else the only option that `name` is a prefix of.
static const Option *lookup(const std::string &name) {
  const Option *hit = nullptr;
  int nhits = 0;
  for (const Option &o : options) {
    if (name == o.name) return &o;
    if (strncmp(o.name, name.c_str(), name.size()) == 0) {
      hit = &o;
      nhits++;
    }
  }
  return nhits == 1 ? hit : nullptr;
}

static bool parse_bool(const std::string &v) {
  if (v == "1" || v == "true" || v == "on" || v == "yes") return true;
  if (v == "0" || v == "false" || v == "off" || v == "no") return false;
  die("bad boolean '" + v + "'");
}

void parse_args(int argc, char **argv) {
  for (int i = 1; i < argc; i++) {
    std::string arg = argv[i];
    if (arg.rfind("--", 0) != 0) die("unexpected argument '" + arg + "'");
    arg = arg.substr(2);

    std::string name = arg, val;
    bool has_val = false;
    if (size_t eq = arg.find('='); eq != std::string::npos) {
      name = arg.substr(0, eq);
      val = arg.substr(eq + 1);
      has_val = true;
    }

    const Option *o = lookup(name);
    if (!o) die("unknown or ambiguous option '--" + name + "'");
    if (o->type == HELP) {
      printf("Program options:\n");
      for (const Option &opt : options) printf("  --%-16s %s\n", opt.name, opt.help);
      exit(1);
    }
    if (!has_val) {
      if (i + 1 >= argc) die(std::string("option '--") + o->name + "' needs a value");
      val = argv[++i];
    }

    char *end = nullptr;
    const char *v = val.c_str();
    switch (o->type) {
      case U32: *(uint32_t *)o->dst = strtoul(v, &end, 0); break;
      case U64: *(uint64_t *)o->dst = strtoull(v, &end, 0); break;
      case I64: *(int64_t *)o->dst = strtoll(v, &end, 0); break;
      case DBL: *(double *)o->dst = strtod(v, &end); break;
      case BOOL: *(bool *)o->dst = parse_bool(val); break;
      case HELP: break;
    }
    if (end && *end) die("bad value '" + val + "' for '--" + o->name + "'");
  }
}
