# SPDX-License-Identifier: Apache-2.0

cmake_minimum_required(VERSION 3.28.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})

target_include_directories(app PRIVATE ${ZEPHYR_BASE}/subsys/net/lib)

project(esp32p4_ptp)
target_sources(app PRIVATE src/main.c src/ptp_diag.c)
target_sources_ifdef(CONFIG_SMP app PRIVATE src/core1_test.c)
