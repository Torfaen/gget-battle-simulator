/* Optional, process-local diagnostics for the pinned ARM64 build. */

#include "android_runtime.h"

extern int printf(const char *format, ...);
extern int fflush(void *stream);
extern void *mmap(void *, unsigned long, int, int, int, long);
extern int mprotect(void *, unsigned long, int);
extern int munmap(void *, unsigned long);
extern void *dlopen(const char *, int);
extern void *dlsym(void *, const char *);

static unsigned long runtime_base;
static void (*throw_cpp_exception)(void *, void *, void (*)(void *));
static int (*unwind_backtrace)(int (*)(void *, void *), void *);
static unsigned long (*unwind_ip)(void *);
void *null_trace_throw;
void *null_trace_fallthrough;
void null_trace_entry(void);

void report_null_caller(unsigned long return_address) {
    printf("REUSE null_reference_at 0x%lx\n", return_address - runtime_base - 4);
    fflush(0);
}

__asm__(
    ".text\n"
    ".p2align 2\n"
    ".global null_trace_entry\n"
    "null_trace_entry:\n"
    "    sub sp, sp, #80\n"
    "    stp x0, x1, [sp]\n"
    "    stp x2, x3, [sp, #16]\n"
    "    stp x4, x5, [sp, #32]\n"
    "    stp x6, x7, [sp, #48]\n"
    "    stp x8, x30, [sp, #64]\n"
    "    mov x0, x30\n"
    "    bl report_null_caller\n"
    "    ldp x0, x1, [sp]\n"
    "    ldp x2, x3, [sp, #16]\n"
    "    ldp x4, x5, [sp, #32]\n"
    "    ldp x6, x7, [sp, #48]\n"
    "    ldp x8, x30, [sp, #64]\n"
    "    add sp, sp, #80\n"
    "    str x30, [sp, #-16]!\n"
    "    adrp x30, null_trace_fallthrough\n"
    "    ldr x30, [x30, :lo12:null_trace_fallthrough]\n"
    "    adrp x16, null_trace_throw\n"
    "    ldr x16, [x16, :lo12:null_trace_throw]\n"
    "    br x16\n"
);

static void flush_instruction(const void *address) {
    __asm__ volatile("dc cvau, %0\n dsb ish\n ic ivau, %0\n dsb ish\n isb"
                     :: "r" (address) : "memory");
}

static int report_throw_frame(void *context, void *state) {
    int *depth = state;
    unsigned long ip = unwind_ip(context);
    unsigned long begin = battle_client_28 ? 0x545bd04 : 0x52b62f4;
    unsigned long end = battle_client_28 ? 0xd5db290 : 0xd169620;
    if (ip >= runtime_base + begin && ip < runtime_base + end) {
        printf(" 0x%lx", ip - runtime_base - 4);
    }
    return ++*depth < 24 ? 0 : 5; /* _URC_NO_REASON / _URC_END_OF_STACK */
}

static __attribute__((noreturn)) void trace_cpp_throw(void *object, void *type, void (*destruct)(void *)) {
    unsigned long site = (unsigned long) __builtin_return_address(0);
    int depth = 0;
    printf("REUSE cpp_throw_at 0x%lx stack", site - runtime_base - 4);
    if (unwind_backtrace) {
        unwind_backtrace(report_throw_frame, &depth);
    }
    printf("\n");
    fflush(0);
    throw_cpp_exception(object, type, destruct);
    __builtin_unreachable();
}

static int trace_cpp_exceptions(void) {
    void *unwinder = dlopen(battle_client_28 ? "libc++_shared.so" : "/system/lib64/libc++.so", 2);
    if (!unwinder) {
        if (battle_client_28) {
            unwinder = dlopen(battle_path("libil2cpp.so"), 2);
        }
    }
    if (!unwinder) {
        return 0;
    }
    unwind_backtrace = dlsym(unwinder, "_Unwind_Backtrace");
    unwind_ip = dlsym(unwinder, "_Unwind_GetIP");
    /* The pinned library's RELRO entry for its own __cxa_throw PLT stub. */
    void **slot = (void **) (runtime_base + (battle_client_28 ? 0xdf3d5e8 : 0xda99940));
    if ((!unwind_backtrace || !unwind_ip) && battle_client_28) {
        /* The statically linked unwinder is private in this build. */
        unwind_backtrace = 0;
    } else if (!unwind_backtrace || !unwind_ip) {
        return 0;
    }
    unsigned long original_throw = battle_client_28 ? 0x5ab8f28 : 0x58f1e64;
    if (*slot != (void *) (runtime_base + original_throw)) {
        return 0;
    }
    const unsigned long page_size = 4096;
    void *page = (void *) ((unsigned long) slot & ~(page_size - 1));
    if (mprotect(page, page_size, 3)) {
        return 0;
    }
    throw_cpp_exception = *slot;
    *slot = (void *) trace_cpp_throw;
    if (mprotect(page, page_size, 1)) {
        return 0;
    }
    printf("REUSE cpp_throw_trace_enabled\n");
    return 1;
}

int trace_null_references(const void *runtime_symbol) {
    struct library_address info;
    if (!dladdr(runtime_symbol, &info)) {
        return 0;
    }
    runtime_base = (unsigned long) info.base;
    if (battle_client_28) {
        return trace_cpp_exceptions();
    }
    unsigned long entry_rva = 0x57fb394, throw_rva = 0x57fce54;
    unsigned int *entry = (unsigned int *) (runtime_base + entry_rva);
    const unsigned long page_size = 4096;
    const unsigned int throw_call = 0x94000000 | ((throw_rva - entry_rva - 4) >> 2);
    if (entry[0] != 0xf81f0ffe || entry[1] != throw_call) {
        /* str x30, [sp, #-16]!; bl RaiseNullReferenceException */
        return 0;
    }
    /* A page just past the ELF can be reached by ADRP and BR. */
    const int map_flags = 0x100022; /* FIXED_NOREPLACE | ANONYMOUS | PRIVATE */
    unsigned int *stub = (void *) -1;
    for (unsigned long offset = 0x10000000; stub == (void *) -1 && offset < 0x40000000;
         offset += 0x02000000) {
        stub = mmap((void *) (runtime_base + offset), page_size, 3, map_flags, -1, 0);
    }
    if (stub == (void *) -1) {
        return 0;
    }
    void *page = (void *) ((unsigned long) entry & ~(page_size - 1));
    long page_delta = ((long) stub - (long) page) / (long) page_size;
    if (page_delta < -0x100000 || page_delta >= 0x100000) {
        munmap(stub, page_size);
        return 0;
    }
    stub[0] = 0x58000050; /* ldr x16, [pc, #8] */
    stub[1] = 0xd61f0200; /* br x16 */
    *(void **) (stub + 2) = (void *) null_trace_entry;
    null_trace_throw = (void *) (runtime_base + throw_rva);
    null_trace_fallthrough = entry + 2;
    flush_instruction(stub);
    if (mprotect(stub, page_size, 5) || mprotect(page, page_size, 7)) {
        munmap(stub, page_size);
        return 0;
    }
    unsigned long immediate = (unsigned long) page_delta & 0x1fffff;
    entry[0] = 0x90000010 | ((immediate & 3) << 29) | ((immediate >> 2) << 5); /* adrp x16, stub */
    entry[1] = 0xd61f0200; /* br x16 */
    flush_instruction(entry);
    if (mprotect(page, page_size, 5)) {
        return 0;
    }
    printf("REUSE null_trace_enabled\n");
    if (!trace_cpp_exceptions()) {
        printf("REUSE cpp_throw_trace_unavailable\n");
    }
    return 1;
}
