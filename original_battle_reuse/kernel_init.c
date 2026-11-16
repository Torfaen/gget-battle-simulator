/* PID 1 for the isolated ARM64 Linux test guest. */

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <termios.h>
#include <unistd.h>

static int copy_stage_input(const char *source, const char *target) {
    FILE *input = fopen(source, "rb");
    if (!input) {
        if (errno == ENOENT) {
            return 1;
        }
        perror(source);
        return 0;
    }
    FILE *output = fopen(target, "wb");
    if (!output) {
        perror(target);
        fclose(input);
        return 0;
    }
    unsigned char buffer[8192];
    size_t count;
    int success = 1;
    while ((count = fread(buffer, 1, sizeof(buffer), input)) != 0) {
        if (fwrite(buffer, 1, count, output) != count) {
            success = 0;
            break;
        }
    }
    success = success && !ferror(input);
    success = fclose(output) == 0 && success;
    fclose(input);
    if (!success) {
        fprintf(stderr, "Could not copy stage input %s\n", target);
    }
    return success;
}

static int load_stage_inputs(void) {
    static const char *const names[] = {
        "StageExpected.tsv", "StageEntity.json", "NpcExpected.tsv",
        "PlayerDatabase.bytes", "PlayerSelection.tsv", "PlayerExpected.tsv", "CustomFormation.enabled",
        "BattleTimeline.enabled", "BattleTimeline.bin",
    };
    int module = open("/lib/qemu_fw_cfg.ko", O_RDONLY);
    if (module < 0) {
        perror("open qemu_fw_cfg");
        return 0;
    }
    long loaded = syscall(SYS_finit_module, module, "", 0);
    close(module);
    if (loaded < 0) {
        perror("load qemu_fw_cfg");
        return 0;
    }
    const char *directory = "/sys/firmware/qemu_fw_cfg/by_name/opt/battle";
    if (access(directory, F_OK) != 0) {
        perror(directory);
        return 0;
    }
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); ++i) {
        char source[128], target[96];
        snprintf(source, sizeof(source), "%s/%s/raw", directory, names[i]);
        snprintf(target, sizeof(target), "/game/stage/%s", names[i]);
        if (!copy_stage_input(source, target)) {
            return 0;
        }
    }
    return 1;
}

static int start(const char *mode) {
    if (mount("proc", "/proc", "proc", 0, NULL) && errno != EBUSY) {
        perror("mount proc");
        return 126;
    }
    if (mount("sysfs", "/sys", "sysfs", 0, NULL) && errno != EBUSY) {
        perror("mount sysfs");
        return 126;
    }
    if (mount("devtmpfs", "/dev", "devtmpfs", 0, NULL) && errno != EBUSY) {
        perror("mount devtmpfs");
        return 126;
    }
    if (strcmp(mode, "link") && strcmp(mode, "init") && !load_stage_inputs()) {
        return 126;
    }
    const char *program = "/bin/battle_reuse";
    struct termios terminal;
    if (!tcgetattr(STDIN_FILENO, &terminal)) {
        terminal.c_lflag &= ~(ECHO | ECHONL);
        tcsetattr(STDIN_FILENO, TCSANOW, &terminal);
    }
    char *args[] = {(char *) program, (char *) mode, NULL};
    char *environment[] = {"LD_LIBRARY_PATH=/game:/system/lib64", NULL};
    printf("REUSE kernel_guest_start %s\n", mode);
    fflush(stdout);
    pid_t child = fork();
    if (child == 0) {
        execve(program, args, environment);
        perror("execve Android guest");
        _exit(126);
    }
    if (child < 0) {
        perror("fork");
        return 126;
    }
    int status;
    while (waitpid(child, &status, 0) < 0) {
        if (errno != EINTR) {
            perror("waitpid");
            return 126;
        }
    }
    if (WIFSIGNALED(status)) {
        printf("REUSE kernel_guest_signal %d\n", WTERMSIG(status));
        return 128 + WTERMSIG(status);
    }
    return WEXITSTATUS(status);
}

int main(int argc, char **argv) {
    const char *mode = argc > 1 ? argv[1] : "init";
    int status = start(mode);
    printf("REUSE kernel_guest_exit %d\n", status);
    fflush(stdout);
    reboot(RB_POWER_OFF);
    for (;;) {
        pause();
    }
}
