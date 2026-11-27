#!/bin/bash

OUT="redmi15c_diagnostic.txt"

{
    echo "========== DEVICE INFO =========="
    adb shell getprop ro.product.model
    adb shell getprop ro.product.device
    adb shell getprop ro.build.version.release
    adb shell getprop ro.build.version.incremental
    adb shell getprop ro.soc.model

    echo "========== CPU INFO =========="
    adb shell cat /proc/cpuinfo

    echo "========== MEMORY =========="
    adb shell free -m
    adb shell cat /proc/meminfo

    echo "========== CPU PROCESSES =========="
    adb shell top -b -n 3 -d 2

    echo "========== STORAGE =========="
    adb shell df -h /data
    adb shell cat /proc/diskstats

    echo "========== APP MEMORY =========="
    adb shell dumpsys meminfo

    echo "========== THERMAL =========="
    adb shell dumpsys thermalservice

    echo "========== BATTERY =========="
    adb shell dumpsys battery

    echo "========== CPU USAGE =========="
    adb shell dumpsys cpuinfo

    echo "========== RUNNING SERVICES =========="
    adb shell dumpsys activity services

    echo "========== SYSTEM PACKAGES =========="
    adb shell pm list packages -s

    echo "========== USER PACKAGES =========="
    adb shell pm list packages -3

    echo "========== DISABLED PACKAGES =========="
    adb shell pm list packages -d

    echo "========== GRAPHICS =========="
    adb shell dumpsys gfxinfo com.miui.home

    echo "========== MEMORY PRESSURE =========="
    adb shell cat /proc/pressure/memory

    echo "========== IO PRESSURE =========="
    adb shell cat /proc/pressure/io

    echo "========== CPU PRESSURE =========="
    adb shell cat /proc/pressure/cpu

} > "$OUT" 2>&1

echo "Diagnostic saved: $OUT"
