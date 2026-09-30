/*
 * SMP experiment: a thread pinned to CPU1 that polls the EMAC's internal
 * PPS level (HP_SYSTEM_GMAC_CTRL0.PTP_PPS). On v1.3 silicon that signal
 * can't reach a pin through the GPIO matrix, so a dedicated core copying it
 * out is one way to get a PPS output. This measures how tight such a loop
 * can be; it doesn't drive any pin.
 *
 * Shell: `core1` prints per-CPU busy % since the previous call and the
 * thread's counters.
 */

#include <zephyr/arch/riscv/csr.h>
#include <zephyr/kernel.h>
#include <zephyr/shell/shell.h>
#include <zephyr/sys/sys_io.h>

#define EMAC_PTP_PPS_CTRL_REG 0x5009872CUL /* EMAC PTP base + 0x2c */
#define PPS_CMD0_MASK         0xFU
#define PPS_CMD0_1HZ          1U /* 1 Hz square wave with digital rollover */
#define HP_SYS_GMAC_CTRL0_REG 0x500E514CUL /* bit 0: PTP_PPS level (RO) */
#define CPU_HZ                400000000ULL
#define TEST_CPU              1

static struct {
	uint64_t iterations;
	uint64_t on_cpu[CONFIG_MP_MAX_NUM_CPUS];
	uint32_t period_min;
	uint32_t period_max;
	uint64_t period_sum;
	uint32_t pps_edges;
} stats;

static struct k_spinlock stats_lock;

K_THREAD_STACK_DEFINE(core1_stack, 1024);
static struct k_thread core1_thread;

static void core1_loop(void *p1, void *p2, void *p3)
{
	uint32_t prev = csr_read(mcycle);
	int level = sys_read32(HP_SYS_GMAC_CTRL0_REG) & 1;

	ARG_UNUSED(p1);
	ARG_UNUSED(p2);
	ARG_UNUSED(p3);

	while (true) {
		int now_level = sys_read32(HP_SYS_GMAC_CTRL0_REG) & 1;
		uint32_t now = csr_read(mcycle);
		uint32_t period = now - prev;
		k_spinlock_key_t key = k_spin_lock(&stats_lock);

		prev = now;
		stats.iterations++;
		stats.on_cpu[arch_curr_cpu()->id]++;
		stats.period_sum += period;
		if (period < stats.period_min) {
			stats.period_min = period;
		}
		if (period > stats.period_max) {
			stats.period_max = period;
		}
		if (now_level != level) {
			stats.pps_edges++;
			level = now_level;
		}
		k_spin_unlock(&stats_lock, key);
	}
}

static int core1_init(void)
{
	sys_write32((sys_read32(EMAC_PTP_PPS_CTRL_REG) & ~PPS_CMD0_MASK) | PPS_CMD0_1HZ,
		    EMAC_PTP_PPS_CTRL_REG);

	stats.period_min = UINT32_MAX;

	k_thread_create(&core1_thread, core1_stack, K_THREAD_STACK_SIZEOF(core1_stack),
			core1_loop, NULL, NULL, NULL, K_LOWEST_APPLICATION_THREAD_PRIO, 0,
			K_FOREVER);
	k_thread_name_set(&core1_thread, "core1_test");
	k_thread_cpu_pin(&core1_thread, TEST_CPU);
	k_thread_start(&core1_thread);

	return 0;
}

SYS_INIT(core1_init, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);

static k_thread_runtime_stats_t prev_cpu[CONFIG_MP_MAX_NUM_CPUS];

static unsigned int pct(uint64_t part, uint64_t whole)
{
	return whole ? (unsigned int)(part * 100 / whole) : 0;
}

static uint32_t cyc_to_ns(uint64_t cyc)
{
	return (uint32_t)(cyc * 1000000000ULL / CPU_HZ);
}

static int cmd_core1(const struct shell *sh, size_t argc, char **argv)
{
	k_thread_runtime_stats_t cur, thr;
	k_spinlock_key_t key;
	typeof(stats) snap;

	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	for (int cpu = 0; cpu < CONFIG_MP_MAX_NUM_CPUS; cpu++) {
		k_thread_runtime_stats_cpu_get(cpu, &cur);
		uint64_t all = cur.execution_cycles - prev_cpu[cpu].execution_cycles;
		uint64_t busy = cur.total_cycles - prev_cpu[cpu].total_cycles;

		shell_print(sh, "cpu%d: busy %u%% since last call", cpu, pct(busy, all));
		prev_cpu[cpu] = cur;
	}

	k_thread_runtime_stats_get(&core1_thread, &thr);

	key = k_spin_lock(&stats_lock);
	snap = stats;
	k_spin_unlock(&stats_lock, key);

	shell_print(sh, "core1_test: iterations %llu (cpu0 %llu, cpu1 %llu), runtime cycles %llu",
		    snap.iterations, snap.on_cpu[0], snap.on_cpu[1], thr.execution_cycles);
	if (snap.iterations > 0) {
		shell_print(sh, "core1_test: loop period min %u ns avg %u ns max %u ns",
			    cyc_to_ns(snap.period_min),
			    cyc_to_ns(snap.period_sum / snap.iterations),
			    cyc_to_ns(snap.period_max));
	}
	shell_print(sh, "core1_test: PPS edges seen %u", snap.pps_edges);

	return 0;
}

SHELL_CMD_REGISTER(core1, NULL, "CPU1 pinned-thread test stats", cmd_core1);
