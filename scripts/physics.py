"""Physical models behind the verification thresholds.

A threshold that is not derived from physics is an opinion. `max_skew_mm: 2.5`
means nothing without an edge rate; a 0.59 W dissipation means nothing without a
package. These tables and formulas let the checks say *why* something fails.
"""
from __future__ import annotations

# Junction-to-ambient thermal resistance, degC/W. Two figures per package:
# a bare-minimum-copper case and a realistic case with a modest ground pour.
# Vendor datasheets vary; these are conservative mid-range values, and any
# serious design should substitute the figure for its own footprint and stackup.
THETA_JA = {
    'SOT-23':      (300, 200), 'SOT-23-3':  (300, 200),
    'SOT-23-5':    (250, 160), 'SOT-23-6':  (250, 160),
    'SOT-223':     (110,  62), 'SOT-89':    (140,  90),
    'TO-252':       (92,  50), 'DPAK':       (92,  50),
    'TO-263':       (70,  35), 'D2PAK':      (70,  35),
    'SOIC-8':      (120,  90), 'MSOP-8':    (185, 140),
    'DFN-6':       (110,  60), 'QFN-16':     (60,  40),
    'WSON-8':       (65,  42),
    '0402': (500, 400), '0603': (400, 320), '0805': (330, 260), '1206': (250, 200),
}

# Interface skew budgets in mm of FR-4 outer-layer trace, from common practice.
# rise_ps is the 10-90% edge used to justify the figure; propagation on an outer
# layer is about 6.0 ps/mm (eps_eff ~ 3.2).
INTERFACES = {
    'usb2.0-ls':    {'rate_mbps': 1.5,   'rise_ps': 75000, 'skew_mm': 40.0},
    'usb2.0-fs':    {'rate_mbps': 12,    'rise_ps': 4000,  'skew_mm': 20.0},
    'usb2.0-hs':    {'rate_mbps': 480,   'rise_ps': 500,   'skew_mm': 1.25},
    'usb3.0':       {'rate_mbps': 5000,  'rise_ps': 60,    'skew_mm': 0.13},
    'ethernet-100': {'rate_mbps': 100,   'rise_ps': 4000,  'skew_mm': 12.0},
    'ethernet-1000':{'rate_mbps': 1000,  'rise_ps': 400,   'skew_mm': 1.25},
    'hdmi':         {'rate_mbps': 3400,  'rise_ps': 80,    'skew_mm': 0.25},
    'lvds':         {'rate_mbps': 655,   'rise_ps': 300,   'skew_mm': 1.0},
    'mipi-dsi':     {'rate_mbps': 1500,  'rise_ps': 150,   'skew_mm': 0.5},
    'can':          {'rate_mbps': 1,     'rise_ps': 50000, 'skew_mm': 30.0},
    'rs485':        {'rate_mbps': 10,    'rise_ps': 20000, 'skew_mm': 25.0},
    'sata':         {'rate_mbps': 6000,  'rise_ps': 50,    'skew_mm': 0.13},
}

PROP_PS_PER_MM = 6.0        # FR-4 microstrip, outer layer


def skew_budget_mm(interface: str | None, rise_ps: float | None = None,
                   fallback: float = 2.5) -> tuple[float, str]:
    """Allowed intra-pair skew, and the reasoning for it."""
    if interface and interface in INTERFACES:
        i = INTERFACES[interface]
        return i['skew_mm'], (f"{interface}: {i['rate_mbps']} Mbps, ~{i['rise_ps']} ps edge "
                              f"-> {i['skew_mm']} mm budget")
    if rise_ps:
        mm = round(0.02 * rise_ps / PROP_PS_PER_MM, 2)
        return mm, f"derived: 2% of a {rise_ps} ps edge at {PROP_PS_PER_MM} ps/mm"
    return fallback, f"no interface declared - falling back to {fallback} mm"


def junction_temp(power_w: float, package: str, ambient_c: float = 25.0,
                  has_pour: bool = True, theta_ja: float | None = None):
    """Junction temperature and the theta_JA used to get there."""
    if theta_ja is None:
        pair = THETA_JA.get(package)
        if not pair:
            return None, None
        theta_ja = pair[1] if has_pour else pair[0]
    return ambient_c + power_w * theta_ja, theta_ja


def ldo_dissipation_w(vin: float, vout: float, load_a: float, iq_a: float = 0.0) -> float:
    """LDO burns the voltage difference as heat - efficiency is vout/vin."""
    return (vin - vout) * load_a + vin * iq_a


def ipc2221_width_mm(current_a: float, temp_rise_c: float = 10.0,
                     copper_oz: float = 1.0, external: bool = True) -> float:
    k = 0.048 if external else 0.024
    area_mils2 = (current_a / (k * (temp_rise_c ** 0.44))) ** (1 / 0.725)
    return round(area_mils2 / (copper_oz * 1.378) * 0.0254, 3)
