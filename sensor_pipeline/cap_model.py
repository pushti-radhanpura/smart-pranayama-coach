"""
cap_model.py
============
Capacitive respiration sensor model.
Calculates mechanical overlap, sensor capacitance, and circuit output voltage.
Refactored into importable functions with constants preserved.
"""
from __future__ import annotations

import numpy as np

# ==================================
# 1. SENSOR & CIRCUIT CONSTANTS
# ==================================
epsilon_0 = 8.854e-12
epsilon_r = 3.0
width = 20e-3
plate_length = 60e-3
initial_overlap = 40e-3
separation = 1e-3
eta = 1.0

Vin = 5.0                 # Input voltage (V)
R = 1e6                   # Resistance (1 MOhm)
f = 10000                 # Carrier frequency (10 kHz)
omega = 2 * np.pi * f     # Angular frequency (rad/s)


# ==================================
# 2. PHYSICS FUNCTIONS
# ==================================
def calculate_overlap(belt_expansion_mm):
    """Calculates plate overlap length (m) from belt expansion (mm)."""
    expansion = np.array(belt_expansion_mm) * 1e-3
    plate_displacement = eta * expansion
    overlap = initial_overlap - plate_displacement
    overlap = np.clip(overlap, 0, plate_length)
    return overlap


def calculate_capacitance(belt_expansion_mm):
    """Calculates sensor capacitance (Farads) from belt expansion (mm)."""
    overlap = calculate_overlap(belt_expansion_mm)
    area = width * overlap
    capacitance = epsilon_0 * epsilon_r * area / separation
    return capacitance


def calculate_voltage(capacitance):
    """Calculates divider output voltage amplitude (V) from capacitance (Farads)."""
    cap = np.asarray(capacitance, dtype=float)
    vout = Vin / np.sqrt(1.0 + (omega * R * cap) ** 2)
    return vout


# ==================================
# 3. INTERACTIVE CLI
# ==================================
if __name__ == "__main__":
    import matplotlib.pyplot as plt

    print("CAPACITIVE RESPIRATION SENSOR MODEL")
    try:
        user_input = input("Enter belt expansion values in mm (comma separated): ")
        if not user_input.strip():
            user_input = "0, 2, 4, 6, 8, 10"
            print(f"Using default expansion values: {user_input}")
    except (EOFError, KeyboardInterrupt):
        user_input = "0, 2, 4, 6, 8, 10"
        print(f"Using default expansion values: {user_input}")

    expansion = np.array([float(i.strip()) for i in user_input.split(",") if i.strip()])
    overlap = calculate_overlap(expansion)
    capacitance = calculate_capacitance(expansion)
    capacitance_pF = capacitance * 1e12
    voltage = calculate_voltage(capacitance)

    print("\nMechanical and Capacitance Results:")
    for i in range(len(expansion)):
        print(
            f"Expansion: {expansion[i]:.2f} mm | "
            f"Overlap: {overlap[i]*1000:.2f} mm | "
            f"Capacitance: {capacitance_pF[i]:.2f} pF | "
            f"Voltage: {voltage[i]:.3f} V"
        )

    # Plot capacitance vs expansion
    plt.figure(figsize=(8, 4))
    plt.subplot(1, 2, 1)
    plt.plot(expansion, capacitance_pF, marker="o", color="#b66c53")
    plt.xlabel("Belt expansion (mm)")
    plt.ylabel("Capacitance (pF)")
    plt.title("Capacitance vs Expansion")
    plt.grid(True)

    plt.subplot(1, 2, 2)
    plt.plot(expansion, voltage, marker="o", color="#557448")
    plt.xlabel("Belt Expansion (mm)")
    plt.ylabel("Output Voltage (V)")
    plt.title("Output Voltage vs Expansion")
    plt.grid(True)
    plt.tight_layout()
    plt.show()

    # Export sample PWL
    dt = 0.1
    time_arr = np.arange(len(capacitance)) * dt
    np.savetxt(
        "cap_pwl.txt",
        np.column_stack((time_arr, capacitance_pF)),
        fmt="%.10e",
        delimiter="\t",
    )
    print("Exported cap_pwl.txt successfully.")
