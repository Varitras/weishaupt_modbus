"""Heat pump constants and definitions."""

import copy
from typing import Any

from custom_components.weishaupt_modbus.const import DEVICES, FORMATS, TYPES
from custom_components.weishaupt_modbus.items import ModbusItem, StatusItem
from custom_components.weishaupt_modbus.weishaupt_modbus_api.calculations import (
    heat_output,
    performance_factor,
    spread,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolumeFlowRate,
)

# Topic prefix per device: the attribute name of each DEVICES value, so a
# device added to DeviceConstants cannot silently prefix its topics with UK.
reverse_device_list: dict[str, str] = {
    value: name
    for name, value in vars(type(DEVICES)).items()
    if isinstance(value, str) and not name.startswith("_")
}

################################################################################
# The values each status register can hold; their texts live in the
# translations under each translation_key
################################################################################

# fmt: off
SYS_FEHLER: list[StatusItem] = [
    StatusItem(number=65535, translation_key="sys_fehler_65535"),
    StatusItem(number=1, translation_key="sys_fehler_1"),
    StatusItem(number=2, translation_key="sys_fehler_2"),
    StatusItem(number=3, translation_key="sys_fehler_3"),
    StatusItem(number=4, translation_key="sys_fehler_4"),
    StatusItem(number=5, translation_key="sys_fehler_5"),
    StatusItem(number=6, translation_key="sys_fehler_6"),
    StatusItem(number=7, translation_key="sys_fehler_7"),
    StatusItem(number=8, translation_key="sys_fehler_8"),
    StatusItem(number=9, translation_key="sys_fehler_9"),
    StatusItem(number=10, translation_key="sys_fehler_10"),
    StatusItem(number=11, translation_key="sys_fehler_11"),
    StatusItem(number=12, translation_key="sys_fehler_12"),
    StatusItem(number=13, translation_key="sys_fehler_13"),
    StatusItem(number=14, translation_key="sys_fehler_14"),
    StatusItem(number=15, translation_key="sys_fehler_15"),
    StatusItem(number=16, translation_key="sys_fehler_16"),
    StatusItem(number=17, translation_key="sys_fehler_17"),
    StatusItem(number=18, translation_key="sys_fehler_18"),
    StatusItem(number=19, translation_key="sys_fehler_19"),
    StatusItem(number=20, translation_key="sys_fehler_20"),
    StatusItem(number=21, translation_key="sys_fehler_21"),
    StatusItem(number=22, translation_key="sys_fehler_22"),
    StatusItem(number=23, translation_key="sys_fehler_23"),
    StatusItem(number=24, translation_key="sys_fehler_24"),
    StatusItem(number=25, translation_key="sys_fehler_25"),
    StatusItem(number=26, translation_key="sys_fehler_26"),
    StatusItem(number=27, translation_key="sys_fehler_27"),
    StatusItem(number=28, translation_key="sys_fehler_28"),
    StatusItem(number=29, translation_key="sys_fehler_29"),
    StatusItem(number=30, translation_key="sys_fehler_30"),
    StatusItem(number=32, translation_key="sys_fehler_32"),
    StatusItem(number=33, translation_key="sys_fehler_33"),
    StatusItem(number=40, translation_key="sys_fehler_40"),
    StatusItem(number=41, translation_key="sys_fehler_41"),
    StatusItem(number=43, translation_key="sys_fehler_43"),
    StatusItem(number=44, translation_key="sys_fehler_44"),
    StatusItem(number=47, translation_key="sys_fehler_47"),
    StatusItem(number=50, translation_key="sys_fehler_50"),
    StatusItem(number=51, translation_key="sys_fehler_51"),
    StatusItem(number=52, translation_key="sys_fehler_52"),
    StatusItem(number=53, translation_key="sys_fehler_53"),
    StatusItem(number=54, translation_key="sys_fehler_54"),
    StatusItem(number=55, translation_key="sys_fehler_55"),
    StatusItem(number=56, translation_key="sys_fehler_56"),
    StatusItem(number=57, translation_key="sys_fehler_57"),
    StatusItem(number=58, translation_key="sys_fehler_58"),
    StatusItem(number=59, translation_key="sys_fehler_59"),
    StatusItem(number=60, translation_key="sys_fehler_60"),
    StatusItem(number=61, translation_key="sys_fehler_61"),
    StatusItem(number=64, translation_key="sys_fehler_64"),
    StatusItem(number=65, translation_key="sys_fehler_65"),
    StatusItem(number=66, translation_key="sys_fehler_66"),
    StatusItem(number=67, translation_key="sys_fehler_67"),
    StatusItem(number=70, translation_key="sys_fehler_70"),
    StatusItem(number=71, translation_key="sys_fehler_71"),
    StatusItem(number=72, translation_key="sys_fehler_72"),
    StatusItem(number=73, translation_key="sys_fehler_73"),
    StatusItem(number=74, translation_key="sys_fehler_74"),
    StatusItem(number=75, translation_key="sys_fehler_75"),
    StatusItem(number=90, translation_key="sys_fehler_90"),
    StatusItem(number=91, translation_key="sys_fehler_91"),
    StatusItem(number=92, translation_key="sys_fehler_92"),
    StatusItem(number=93, translation_key="sys_fehler_93"),
    StatusItem(number=94, translation_key="sys_fehler_94"),
    StatusItem(number=95, translation_key="sys_fehler_95"),
    StatusItem(number=101, translation_key="sys_fehler_101"),
    StatusItem(number=102, translation_key="sys_fehler_102"),
    StatusItem(number=103, translation_key="sys_fehler_103"),
    StatusItem(number=104, translation_key="sys_fehler_104"),
    StatusItem(number=105, translation_key="sys_fehler_105"),
    StatusItem(number=106, translation_key="sys_fehler_106"),
    StatusItem(number=107, translation_key="sys_fehler_107"),
    StatusItem(number=108, translation_key="sys_fehler_108"),
    StatusItem(number=109, translation_key="sys_fehler_109"),
    StatusItem(number=110, translation_key="sys_fehler_110"),
    StatusItem(number=111, translation_key="sys_fehler_111"),
    StatusItem(number=112, translation_key="sys_fehler_112"),
    StatusItem(number=113, translation_key="sys_fehler_113"),
    StatusItem(number=114, translation_key="sys_fehler_114"),
    StatusItem(number=117, translation_key="sys_fehler_117"),
    StatusItem(number=118, translation_key="sys_fehler_118"),
    StatusItem(number=119, translation_key="sys_fehler_119"),
    StatusItem(number=120, translation_key="sys_fehler_120"),
    StatusItem(number=121, translation_key="sys_fehler_121"),
    StatusItem(number=122, translation_key="sys_fehler_122"),
    StatusItem(number=123, translation_key="sys_fehler_123"),
    StatusItem(number=124, translation_key="sys_fehler_124"),
    StatusItem(number=127, translation_key="sys_fehler_127"),
    StatusItem(number=128, translation_key="sys_fehler_128"),
    StatusItem(number=129, translation_key="sys_fehler_129"),
    StatusItem(number=130, translation_key="sys_fehler_130"),
    StatusItem(number=133, translation_key="sys_fehler_133"),
    StatusItem(number=135, translation_key="sys_fehler_135"),
    StatusItem(number=136, translation_key="sys_fehler_136"),
    StatusItem(number=137, translation_key="sys_fehler_137"),
    StatusItem(number=140, translation_key="sys_fehler_140"),
    StatusItem(number=143, translation_key="sys_fehler_143"),
    StatusItem(number=144, translation_key="sys_fehler_144"),
    StatusItem(number=150, translation_key="sys_fehler_150"),
    StatusItem(number=151, translation_key="sys_fehler_151"),
    StatusItem(number=152, translation_key="sys_fehler_152"),
    StatusItem(number=153, translation_key="sys_fehler_153"),
    StatusItem(number=154, translation_key="sys_fehler_154"),
    StatusItem(number=155, translation_key="sys_fehler_155"),
    StatusItem(number=156, translation_key="sys_fehler_156"),
    StatusItem(number=157, translation_key="sys_fehler_157"),
    StatusItem(number=158, translation_key="sys_fehler_158"),
    StatusItem(number=159, translation_key="sys_fehler_159"),
    StatusItem(number=160, translation_key="sys_fehler_160"),
    StatusItem(number=161, translation_key="sys_fehler_161"),
    StatusItem(number=162, translation_key="sys_fehler_162"),
    StatusItem(number=163, translation_key="sys_fehler_163"),
    StatusItem(number=164, translation_key="sys_fehler_164"),
    StatusItem(number=165, translation_key="sys_fehler_165"),
    StatusItem(number=166, translation_key="sys_fehler_166"),
    StatusItem(number=167, translation_key="sys_fehler_167"),
    StatusItem(number=168, translation_key="sys_fehler_168"),
    StatusItem(number=169, translation_key="sys_fehler_169"),
    StatusItem(number=170, translation_key="sys_fehler_170"),
    StatusItem(number=171, translation_key="sys_fehler_171"),
    StatusItem(number=172, translation_key="sys_fehler_172"),
    StatusItem(number=173, translation_key="sys_fehler_173"),
    StatusItem(number=174, translation_key="sys_fehler_174"),
    StatusItem(number=175, translation_key="sys_fehler_175"),
    StatusItem(number=176, translation_key="sys_fehler_176"),
    StatusItem(number=177, translation_key="sys_fehler_177"),
    StatusItem(number=178, translation_key="sys_fehler_178"),
    StatusItem(number=179, translation_key="sys_fehler_179"),
    StatusItem(number=180, translation_key="sys_fehler_180"),
    StatusItem(number=181, translation_key="sys_fehler_181"),
    StatusItem(number=182, translation_key="sys_fehler_182"),
    StatusItem(number=183, translation_key="sys_fehler_183"),
    StatusItem(number=184, translation_key="sys_fehler_184"),
]

# fmt: on

SYS_FEHLERFREI: list[StatusItem] = [
    StatusItem(number=0, translation_key="fehler_aktiv"),
    StatusItem(number=1, translation_key="stoerungsfreier_betrieb"),
]

SYS_BETRIEBSANZEIGE: list[StatusItem] = [
    StatusItem(number=0, translation_key="system_operationmode_undefined"),
    StatusItem(number=1, translation_key="system_operationmode_relaistest"),
    StatusItem(number=2, translation_key="system_operationmode_emergencystop"),
    StatusItem(number=3, translation_key="system_operationmode_diagnosis"),
    StatusItem(number=4, translation_key="system_operationmode_manual"),
    StatusItem(number=5, translation_key="system_operationmode_manualheating"),
    StatusItem(number=6, translation_key="system_operationmode_manualcooling"),
    StatusItem(number=7, translation_key="system_operationmode_manualdefrost"),
    StatusItem(number=8, translation_key="system_operationmode_defrost"),
    StatusItem(number=9, translation_key="system_operationmode_manual2ndheatsource"),
    StatusItem(number=10, translation_key="system_operationmode_evu"),
    StatusItem(number=11, translation_key="system_operationmode_sgtariff"),
    StatusItem(number=12, translation_key="system_operationmode_sgmax"),
    StatusItem(number=13, translation_key="system_operationmode_tariffload"),
    StatusItem(number=14, translation_key="system_operationmode_elevatedoperation"),
    StatusItem(number=15, translation_key="system_operationmode_standbytime"),
    StatusItem(number=16, translation_key="system_operationmode_standby"),
    StatusItem(number=17, translation_key="system_operationmode_rinse"),
    StatusItem(number=18, translation_key="system_operationmode_frosprotection"),
    StatusItem(number=19, translation_key="system_operationmode_heating"),
    StatusItem(number=20, translation_key="system_operationmode_hotwater"),
    StatusItem(number=21, translation_key="system_operationmode_legionellaprotection"),
    StatusItem(number=22, translation_key="system_operationmode_switchheatingcooling"),
    StatusItem(number=23, translation_key="system_operationmode_cooling"),
    StatusItem(number=24, translation_key="system_operationmode_passivecooling"),
    StatusItem(number=25, translation_key="system_operationmode_summer"),
    StatusItem(number=26, translation_key="system_operationmode_swimmingpool"),
    StatusItem(number=27, translation_key="system_operationmode_vacation"),
    StatusItem(number=28, translation_key="system_operationmode_screedprogram"),
    StatusItem(number=29, translation_key="system_operationmode_locked"),
    StatusItem(number=30, translation_key="system_operationmode_lockedat"),
    StatusItem(number=31, translation_key="system_operationmode_lockedsummer"),
    StatusItem(number=32, translation_key="system_operationmode_lockedwinter"),
    StatusItem(number=33, translation_key="system_operationmode_applicationlimit"),
    StatusItem(number=34, translation_key="system_operationmode_lockedcv"),
    StatusItem(number=35, translation_key="system_operationmode_lowering"),
    StatusItem(number=36, translation_key="system_operationmode_regenerativeflow"),
    StatusItem(number=37, translation_key="system_operationmode_heating_sgr3"),
    StatusItem(number=38, translation_key="system_operationmode_cooling_sgr3"),
    StatusItem(number=39, translation_key="system_operationmode_hotwater_sgr3"),
    StatusItem(number=40, translation_key="system_operationmode_heating_sgr4"),
    StatusItem(number=41, translation_key="system_operationmode_cooling_sgr4"),
    StatusItem(number=42, translation_key="system_operationmode_hotwater_sgr4"),
    StatusItem(number=43, translation_key="system_operationmode_oilrecirculation"),
]

SYS_BETRIEBSART: list[StatusItem] = [
    StatusItem(number=0, translation_key="sys_operationmode_automatic"),
    StatusItem(number=1, translation_key="sys_operationmode_heating"),
    StatusItem(number=2, translation_key="sys_operationmode_cooling"),
    StatusItem(number=3, translation_key="sys_operationmode_summer"),
    StatusItem(number=4, translation_key="sys_operationmode_standby"),
    StatusItem(number=5, translation_key="sys_operationmode_2ndheatsource"),
]

HP_BETRIEB: list[StatusItem] = [
    StatusItem(number=0, translation_key="heatpump_operationmode_undefined"),
    StatusItem(number=1, translation_key="heatpump_operationmode_relaistest"),
    StatusItem(number=2, translation_key="heatpump_operationmode_emergencystop"),
    StatusItem(number=3, translation_key="heatpump_operationmode_diagnosis"),
    StatusItem(number=4, translation_key="heatpump_operationmode_manual"),
    StatusItem(number=5, translation_key="heatpump_operationmode_manualheating"),
    StatusItem(number=6, translation_key="heatpump_operationmode_manualcooling"),
    StatusItem(number=7, translation_key="heatpump_operationmode_manualdefrost"),
    StatusItem(number=8, translation_key="heatpump_operationmode_defrost"),
    StatusItem(number=9, translation_key="heatpump_operationmode_manual2ndheatsource"),
    StatusItem(number=10, translation_key="heatpump_operationmode_evu"),
    StatusItem(number=11, translation_key="heatpump_operationmode_sgtariff"),
    StatusItem(number=12, translation_key="heatpump_operationmode_sgmax"),
    StatusItem(number=13, translation_key="heatpump_operationmode_tariffload"),
    StatusItem(number=14, translation_key="heatpump_operationmode_elevatedoperation"),
    StatusItem(number=15, translation_key="heatpump_operationmode_standbytime"),
    StatusItem(number=16, translation_key="heatpump_operationmode_standby"),
    StatusItem(number=17, translation_key="heatpump_operationmode_rinse"),
    StatusItem(number=18, translation_key="heatpump_operationmode_frosprotection"),
    StatusItem(number=19, translation_key="heatpump_operationmode_heating"),
    StatusItem(number=20, translation_key="heatpump_operationmode_hotwater"),
    StatusItem(
        number=21, translation_key="heatpump_operationmode_legionellaprotection"
    ),
    StatusItem(
        number=22, translation_key="heatpump_operationmode_switchheatingcooling"
    ),
    StatusItem(number=23, translation_key="heatpump_operationmode_cooling"),
    StatusItem(number=24, translation_key="heatpump_operationmode_passivecooling"),
    StatusItem(number=25, translation_key="heatpump_operationmode_summer"),
    StatusItem(number=26, translation_key="heatpump_operationmode_swimmingpool"),
    StatusItem(number=27, translation_key="heatpump_operationmode_vacation"),
    StatusItem(number=28, translation_key="heatpump_operationmode_screedprogram"),
    StatusItem(number=29, translation_key="heatpump_operationmode_locked"),
    StatusItem(number=30, translation_key="heatpump_operationmode_lockedat"),
    StatusItem(number=31, translation_key="heatpump_operationmode_lockedsummer"),
    StatusItem(number=32, translation_key="heatpump_operationmode_lockedwinter"),
    StatusItem(number=33, translation_key="heatpump_operationmode_applicationlimit"),
    StatusItem(number=34, translation_key="heatpump_operationmode_lockedcv"),
    StatusItem(number=35, translation_key="heatpump_operationmode_lowering"),
    StatusItem(number=36, translation_key="heatpump_operationmode_regenerativ"),
    StatusItem(number=37, translation_key="heatpump_operationmode_heating_sgr3"),
    StatusItem(number=38, translation_key="heatpump_operationmode_cooling_sgr3"),
    StatusItem(number=39, translation_key="heatpump_operationmode_hotwater_sgr3"),
    StatusItem(number=40, translation_key="heatpump_operationmode_heating_sgr4"),
    StatusItem(number=41, translation_key="heatpump_operationmode_cooling_sgr4"),
    StatusItem(number=42, translation_key="heatpump_operationmode_hotwater_sgr4"),
    StatusItem(number=43, translation_key="heatpump_operationmode_oilrecirculation"),
]

HP_STOERMELDUNG: list[StatusItem] = [
    StatusItem(number=0, translation_key="hp_stoerung"),
    StatusItem(number=1, translation_key="hp_stoerungsfrei"),
]

HP_RUHEMODUS: list[StatusItem] = [
    StatusItem(number=0, translation_key="hp_ruhemodus_0"),
    StatusItem(number=1, translation_key="hp_ruhemodus_1"),
    StatusItem(number=2, translation_key="hp_ruhemodus_2"),
    StatusItem(number=3, translation_key="hp_ruhemodus_3"),
]

HZ_KONFIGURATION: list[StatusItem] = [
    StatusItem(number=0, translation_key="hp_konf_aus"),
    StatusItem(number=1, translation_key="hp_konf_pumpenkreis"),
    StatusItem(number=2, translation_key="hp_konf_mischkreis"),
    StatusItem(number=3, translation_key="hp_konf_sollwert_pumpe_m1"),
]

HZ_ANFORDERUNG: list[StatusItem] = [
    StatusItem(number=0, translation_key="hz_anforderung_aus"),
    StatusItem(number=1, translation_key="hz_anforderung_witterungsgefuehrt"),
    StatusItem(number=2, translation_key="hz_anforderung_raumregelung"),
    StatusItem(number=3, translation_key="hz_anforderung_konstant"),
]

HZ_BETRIEBSART: list[StatusItem] = [
    StatusItem(number=0, translation_key="hz_operationmode_automatic"),
    StatusItem(number=1, translation_key="hz_operationmode_comfort"),
    StatusItem(number=2, translation_key="hz_operationmode_normal"),
    StatusItem(number=3, translation_key="hz_operationmode_lowering"),
    StatusItem(number=4, translation_key="hz_operationmode_standby"),
]

HZ_PARTY_PAUSE: list[StatusItem] = [
    StatusItem(number=1, translation_key="hz_pause_12"),
    StatusItem(number=2, translation_key="hz_pause_11_5"),
    StatusItem(number=3, translation_key="hz_pause_11"),
    StatusItem(number=4, translation_key="hz_pause_10_5"),
    StatusItem(number=5, translation_key="hz_pause_10"),
    StatusItem(number=6, translation_key="hz_pause_9_5"),
    StatusItem(number=7, translation_key="hz_pause_9"),
    StatusItem(number=8, translation_key="hz_pause_8_5"),
    StatusItem(number=9, translation_key="hz_pause_8"),
    StatusItem(number=10, translation_key="hz_pause_7_5"),
    StatusItem(number=11, translation_key="hz_pause_7"),
    StatusItem(number=12, translation_key="hz_pause_6_5"),
    StatusItem(number=13, translation_key="hz_pause_6"),
    StatusItem(number=14, translation_key="hz_pause_5_5"),
    StatusItem(number=15, translation_key="hz_pause_5"),
    StatusItem(number=16, translation_key="hz_pause_4_5"),
    StatusItem(number=17, translation_key="hz_pause_4"),
    StatusItem(number=18, translation_key="hz_pause_3_5"),
    StatusItem(number=19, translation_key="hz_pause_3"),
    StatusItem(number=20, translation_key="hz_pause_2_5"),
    StatusItem(number=21, translation_key="hz_pause_2"),
    StatusItem(number=22, translation_key="hz_pause_1_5"),
    StatusItem(number=23, translation_key="hz_pause_1"),
    StatusItem(number=24, translation_key="hz_pause_0_5"),
    StatusItem(number=25, translation_key="hz_party_pause_auto"),
    StatusItem(number=26, translation_key="hz_party_0_5"),
    StatusItem(number=27, translation_key="hz_party_1"),
    StatusItem(number=28, translation_key="hz_party_1_5"),
    StatusItem(number=29, translation_key="hz_party_2"),
    StatusItem(number=30, translation_key="hz_party_2_5"),
    StatusItem(number=31, translation_key="hz_party_3"),
    StatusItem(number=32, translation_key="hz_party_3_5"),
    StatusItem(number=33, translation_key="hz_party_4"),
    StatusItem(number=34, translation_key="hz_party_4_5"),
    StatusItem(number=35, translation_key="hz_party_5"),
    StatusItem(number=36, translation_key="hz_party_5_5"),
    StatusItem(number=37, translation_key="hz_party_6"),
    StatusItem(number=38, translation_key="hz_party_6_5"),
    StatusItem(number=39, translation_key="hz_party_7"),
    StatusItem(number=40, translation_key="hz_party_7_5"),
    StatusItem(number=41, translation_key="hz_party_8"),
    StatusItem(number=42, translation_key="hz_party_8_5"),
    StatusItem(number=43, translation_key="hz_party_9"),
    StatusItem(number=44, translation_key="hz_party_9_5"),
    StatusItem(number=45, translation_key="hz_party_10"),
    StatusItem(number=46, translation_key="hz_party_10_5"),
    StatusItem(number=47, translation_key="hz_party_11"),
    StatusItem(number=48, translation_key="hz_party_11_5"),
    StatusItem(number=49, translation_key="hz_party_12"),
]

WW_KONFIGURATION: list[StatusItem] = [
    StatusItem(number=0, translation_key="ww_konf_aus"),
    StatusItem(number=1, translation_key="ww_konf_umlenkventil"),
    # 8, not 2: the manufacturer's register list (2022 xlsx) numbers the pump
    # variant 8; nothing answers 2.
    StatusItem(number=8, translation_key="ww_konf_pumpe"),
]

HP_KONFIGURATION: list[StatusItem] = [
    StatusItem(number=0, translation_key="hp_konf_0"),
    StatusItem(number=1, translation_key="hp_conf_1"),
    StatusItem(number=2, translation_key="hp_conf_2"),
    StatusItem(number=3, translation_key="hp_conf_3"),
    StatusItem(number=4, translation_key="hp_conf_4"),
]

WW_PUSH: list[StatusItem] = [
    StatusItem(number=0, translation_key="ww_push_aus"),
]
# Every five minutes up to the 240 the controller accepts (83807301).
for i in range(5, 245, 5):
    WW_PUSH.append(
        StatusItem(number=i, translation_key="ww_push_" + str(object=i)),
    )


W2_STATUS: list[StatusItem] = [
    StatusItem(number=0, translation_key="w2_status_aus"),
    StatusItem(number=1, translation_key="w2_status_ein"),
]

W2_KONFIG: list[StatusItem] = [
    StatusItem(number=0, translation_key="w2_konf_0"),
    StatusItem(number=1, translation_key="w2_konf_1"),
    # A pump without a second heat source answers 255 (live, 2025 firmware).
    StatusItem(number=255, translation_key="w2_konf_255"),
]

EP1_KONFIG: list[StatusItem] = [
    StatusItem(number=5, translation_key="w2_konf_0"),
    StatusItem(number=255, translation_key="w2_konf_1"),
]

EP2_KONFIG: list[StatusItem] = [
    StatusItem(number=6, translation_key="w2_konf_0"),
    StatusItem(number=255, translation_key="w2_konf_1"),
]


IO_KONFIG: list[StatusItem] = [
    StatusItem(number=0, translation_key="io_konf_0"),
    StatusItem(number=1, translation_key="io_konf_1"),
    StatusItem(number=2, translation_key="io_konf_2"),
    StatusItem(number=3, translation_key="io_konf_3"),
    StatusItem(number=4, translation_key="io_konf_4"),
    StatusItem(number=5, translation_key="io_konf_5"),
    StatusItem(number=6, translation_key="io_konf_6"),
    StatusItem(number=7, translation_key="io_konf_7"),
    StatusItem(number=65535, translation_key="io_konf_65535"),
]

IO_KONFIG_IN: list[StatusItem] = [
    StatusItem(number=0, translation_key="io_konf_in_0"),
    StatusItem(number=1, translation_key="io_konf_in_1"),
    StatusItem(number=2, translation_key="io_konf_in_2"),
    StatusItem(number=3, translation_key="io_konf_in_3"),
    StatusItem(number=4, translation_key="io_konf_in_4"),
    StatusItem(number=5, translation_key="io_konf_in_5"),
    StatusItem(number=6, translation_key="io_konf_in_6"),
    StatusItem(number=7, translation_key="io_konf_in_7"),
    StatusItem(number=8, translation_key="io_konf_in_8"),
    StatusItem(number=9, translation_key="io_konf_in_9"),
    StatusItem(number=10, translation_key="io_konf_in_10"),
    StatusItem(number=11, translation_key="io_konf_in_11"),
    StatusItem(number=12, translation_key="io_konf_in_12"),
    StatusItem(number=13, translation_key="io_konf_in_13"),
    StatusItem(number=14, translation_key="io_konf_in_14"),
    StatusItem(number=15, translation_key="io_konf_in_15"),
    StatusItem(number=16, translation_key="io_konf_in_16"),
    StatusItem(number=17, translation_key="io_konf_in_17"),
    StatusItem(number=18, translation_key="io_konf_in_18"),
    StatusItem(number=19, translation_key="io_konf_in_19"),
    StatusItem(number=20, translation_key="io_konf_in_20"),
    StatusItem(number=21, translation_key="io_konf_in_21"),
    StatusItem(number=65535, translation_key="io_konf_in_65535"),
]

#####################################################
# Description of physical units via the status list #
#####################################################

##############################################################################################################################
# A parameter list that can contain the following elements:
# all of the entries are optional on general
# "min": The lowest allowed value of the entity that can be set by the user if read/write.
#        Not needed for SENSOR, SELECT, SENSOR_CALC
# "dynamic_min": The translation key of another entity of this integration. The content of this entity will be used as min val
# "max": The highest allowed value of the entity that can be set by the user if read/write.
#        Not needed for SENSOR, SELECT, SENSOR_CALC
# "dynamic_max": The translation key of another entity of this integration. The content of this entity will be used as max val
# "step": the step when entity is r/w, values can only be set according this step
# "divider": On modbus, values usually are coded as int. To get the real float number,
#            the modbus value has to be divided by this value
# "deviceclass": one of the SensorDeviceClass entries of HomeAssistant definition
# "precision": number of digits after the decimal point
# "unit": the unit of the sensor. When ever possible, use one of the pre-defined units of HomeAssistant
# "stateclass": one of the SensorStateClass types to control storage of the entity in the recorder database
#
# For SENSOR_CALC only:
# "calculation": a function from calculations.py. It receives the entity's own register (divided),
#                then the registers named in "operands" (raw), then the power map if "uses_power_map"
# "operands": translation keys of the other entities whose values the function takes
# "uses_power_map": the function takes the power map as its last argument
##############################################################################################################################

PARAMS_PERCENTAGE: dict[str, Any] = {
    "min": 0,
    "max": 100,
    "precision": 0,
    "unit": PERCENTAGE,
}

PARAMS_ROOMTEMP: dict[str, Any] = {
    "min": 16,
    "max": 28,
    "step": 0.5,
    "divider": 10,
    "deviceclass": SensorDeviceClass.TEMPERATURE,
    "precision": 1,
    "unit": UnitOfTemperature.CELSIUS,
    "stateclass": SensorStateClass.MEASUREMENT,
}


# The constant flow temperatures are NOT room temperatures: the controller's
# menu offers 7-66 degC heating and 7-30 degC cooling (WBB, 2026-09), and
# then clamps to the circuit's own minimum/maximum flow settings, which have
# no Modbus register. A narrower limit here refused a live 35 degC; the
# controller keeps its own limits on top of these.
# off_is_a_setting: the menu offers "Aus" beside the value, reported as 0x8000.
PARAMS_CONSTANT_FLOW_HEATING: dict[str, Any] = {
    **PARAMS_ROOMTEMP,
    "min": 7,
    "max": 66,
    "off_is_a_setting": True,
}
PARAMS_CONSTANT_FLOW_COOLING: dict[str, Any] = {
    **PARAMS_ROOMTEMP,
    "min": 7,
    "max": 30,
    "off_is_a_setting": True,
}


PARAMS_SUMMER_WINTER_SWITCH_TEMP: dict[str, Any] = {
    "min": 3,
    "max": 30,
    # 0x8000 = no summer shutdown (manufacturer's register list)
    "off_is_a_setting": True,
    "step": 0.5,
    "divider": 10,
    "deviceclass": SensorDeviceClass.TEMPERATURE,
    "precision": 1,
    "unit": UnitOfTemperature.CELSIUS,
    "stateclass": SensorStateClass.MEASUREMENT,
}

PARAMS_ROOMTEMP_LOW: dict = {
    "min": 16,
    "max": 28,
    "step": 0.5,
    "divider": 10,
    "deviceclass": SensorDeviceClass.TEMPERATURE,
    "dynamic_max": "raum_soll_temp_normal",
    "precision": 1,
    "unit": UnitOfTemperature.CELSIUS,
    "stateclass": SensorStateClass.MEASUREMENT,
}
PARAMS_ROOMTEMP_LOW2: dict = copy.deepcopy(PARAMS_ROOMTEMP_LOW)
PARAMS_ROOMTEMP_LOW2["dynamic_max"] = PARAMS_ROOMTEMP_LOW2["dynamic_max"] + "2"

PARAMS_ROOMTEMP_LOW3: dict = copy.deepcopy(PARAMS_ROOMTEMP_LOW)
PARAMS_ROOMTEMP_LOW3["dynamic_max"] = PARAMS_ROOMTEMP_LOW3["dynamic_max"] + "3"

PARAMS_ROOMTEMP_LOW4: dict = copy.deepcopy(PARAMS_ROOMTEMP_LOW)
PARAMS_ROOMTEMP_LOW4["dynamic_max"] = PARAMS_ROOMTEMP_LOW4["dynamic_max"] + "4"

PARAMS_ROOMTEMP_LOW5: dict = copy.deepcopy(PARAMS_ROOMTEMP_LOW)
PARAMS_ROOMTEMP_LOW5["dynamic_max"] = PARAMS_ROOMTEMP_LOW5["dynamic_max"] + "5"


PARAMS_ROOMTEMP_MID: dict = {
    "min": 16,
    "max": 28,
    "step": 0.5,
    "divider": 10,
    "deviceclass": SensorDeviceClass.TEMPERATURE,
    "dynamic_min": "raum_soll_temp_absenk",
    "dynamic_max": "raum_soll_temp_komf",
    "precision": 1,
    "unit": UnitOfTemperature.CELSIUS,
    "stateclass": SensorStateClass.MEASUREMENT,
}

PARAMS_ROOMTEMP_MID2: dict = copy.deepcopy(PARAMS_ROOMTEMP_MID)
PARAMS_ROOMTEMP_MID2["dynamic_max"] = PARAMS_ROOMTEMP_MID2["dynamic_max"] + "2"
PARAMS_ROOMTEMP_MID2["dynamic_min"] = PARAMS_ROOMTEMP_MID2["dynamic_min"] + "2"

PARAMS_ROOMTEMP_MID3: dict = copy.deepcopy(PARAMS_ROOMTEMP_MID)
PARAMS_ROOMTEMP_MID3["dynamic_max"] = PARAMS_ROOMTEMP_MID3["dynamic_max"] + "3"
PARAMS_ROOMTEMP_MID3["dynamic_min"] = PARAMS_ROOMTEMP_MID3["dynamic_min"] + "3"

PARAMS_ROOMTEMP_MID4: dict = copy.deepcopy(PARAMS_ROOMTEMP_MID)
PARAMS_ROOMTEMP_MID4["dynamic_max"] = PARAMS_ROOMTEMP_MID4["dynamic_max"] + "4"
PARAMS_ROOMTEMP_MID4["dynamic_min"] = PARAMS_ROOMTEMP_MID4["dynamic_min"] + "4"

PARAMS_ROOMTEMP_MID5: dict = copy.deepcopy(PARAMS_ROOMTEMP_MID)
PARAMS_ROOMTEMP_MID5["dynamic_max"] = PARAMS_ROOMTEMP_MID5["dynamic_max"] + "5"
PARAMS_ROOMTEMP_MID5["dynamic_min"] = PARAMS_ROOMTEMP_MID5["dynamic_min"] + "5"


PARAMS_ROOMTEMP_HIGH: dict = {
    "min": 16,
    "max": 28,
    "step": 0.5,
    "divider": 10,
    "deviceclass": SensorDeviceClass.TEMPERATURE,
    "dynamic_min": "raum_soll_temp_normal",
    "precision": 1,
    "unit": UnitOfTemperature.CELSIUS,
    "stateclass": SensorStateClass.MEASUREMENT,
}

PARAMS_ROOMTEMP_HIGH2: dict = copy.deepcopy(PARAMS_ROOMTEMP_HIGH)
PARAMS_ROOMTEMP_HIGH2["dynamic_min"] = PARAMS_ROOMTEMP_HIGH2["dynamic_min"] + "2"

PARAMS_ROOMTEMP_HIGH3: dict = copy.deepcopy(PARAMS_ROOMTEMP_HIGH)
PARAMS_ROOMTEMP_HIGH3["dynamic_min"] = PARAMS_ROOMTEMP_HIGH3["dynamic_min"] + "3"

PARAMS_ROOMTEMP_HIGH4: dict = copy.deepcopy(PARAMS_ROOMTEMP_HIGH)
PARAMS_ROOMTEMP_HIGH4["dynamic_min"] = PARAMS_ROOMTEMP_HIGH4["dynamic_min"] + "4"

PARAMS_ROOMTEMP_HIGH5: dict = copy.deepcopy(PARAMS_ROOMTEMP_HIGH)
PARAMS_ROOMTEMP_HIGH5["dynamic_min"] = PARAMS_ROOMTEMP_HIGH5["dynamic_min"] + "5"

PARAMS_WATERTEMP: dict = {
    "min": 5.5,
    "max": 60,
    "step": 0.5,
    "divider": 10,
    "deviceclass": SensorDeviceClass.TEMPERATURE,
    "precision": 1,
    "unit": UnitOfTemperature.CELSIUS,
    "stateclass": SensorStateClass.MEASUREMENT,
}

# Lower bounds from the controller's own menu (WBB, 2026-09): lowering from
# 10 degC, normal from 20 degC. The upper bound of "normal" is the DHW
# maximum temperature setting, which has no register and itself goes up to
# 80 degC - so 80 is the widest, and the controller clamps below it.
PARAMS_WATERTEMP_LOW: dict = {
    "min": 10,
    "max": 80,
    "step": 0.5,
    "divider": 10,
    "deviceclass": SensorDeviceClass.TEMPERATURE,
    "dynamic_max": "ww_normal",
    "precision": 1,
    "unit": UnitOfTemperature.CELSIUS,
    "stateclass": SensorStateClass.MEASUREMENT,
}

PARAMS_WATERTEMP_HIGH: dict = {
    "min": 20,
    "max": 80,
    "step": 0.5,
    "divider": 10,
    "deviceclass": SensorDeviceClass.TEMPERATURE,
    "dynamic_min": "ww_absenk",
    "precision": 1,
    "unit": UnitOfTemperature.CELSIUS,
    "stateclass": SensorStateClass.MEASUREMENT,
}


PARAMS_SGREADYTEMP: dict = {
    "min": 0,
    "max": 30,
    "off_is_a_setting": True,
    "step": 0.5,
    "divider": 10,
    "deviceclass": SensorDeviceClass.TEMPERATURE,
    "precision": 1,
    "unit": UnitOfTemperature.CELSIUS,
    "stateclass": SensorStateClass.MEASUREMENT,
}

PARAMS_BIVALENZTEMP: dict = {
    "min": -20,
    "max": 40,
    "step": 0.5,
    "divider": 10,
    "deviceclass": SensorDeviceClass.TEMPERATURE,
    "precision": 1,
    "unit": UnitOfTemperature.CELSIUS,
    "stateclass": SensorStateClass.MEASUREMENT,
}

PARAMS_STDTEMP: dict = {
    "min": -60,
    "max": 100,
    "step": 0.5,
    "divider": 10,
    "deviceclass": SensorDeviceClass.TEMPERATURE,
    "precision": 1,
    "unit": UnitOfTemperature.CELSIUS,
    "stateclass": SensorStateClass.MEASUREMENT,
}


PARAMS_HZKENNLINIE: dict = {
    "min": 0.05,
    "max": 1.5,
    "step": 0.05,
    "divider": 100,
    "precision": 2,
    "stateclass": SensorStateClass.MEASUREMENT,
}

PARAMS_FLOWRATE: dict = {
    "min": 0,
    "max": 5,
    "step": 0.1,
    "divider": 100,
    "precision": 2,
    "unit": UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
    "stateclass": SensorStateClass.MEASUREMENT,
}

PARAMS_ENERGY: dict[str, Any] = {
    "min": 0,
    "max": 999999999999,
    "deviceclass": SensorDeviceClass.ENERGY,
    "precision": 0,
    "unit": UnitOfEnergy.KILO_WATT_HOUR,
    "stateclass": SensorStateClass.TOTAL_INCREASING,
}
# Yesterday's total is a snapshot that changes once a day and may fall. As a
# TOTAL_INCREASING sensor every fall counted as a meter reset, and the energy
# dashboard summed phantom energy. No long-term statistics for it.
PARAMS_ENERGY_YESTERDAY: dict[str, Any] = {**PARAMS_ENERGY, "stateclass": None}


PARAMS_CALCPOWER: dict = {
    "min": 0,
    "max": 50000,
    "operands": ("luftansautgemp", "vl_temp"),
    "uses_power_map": True,
    "deviceclass": SensorDeviceClass.POWER,
    "precision": 0,
    "unit": UnitOfPower.WATT,
    "stateclass": SensorStateClass.MEASUREMENT,
    "calculation": heat_output,
}

# 33126 is in no Weishaupt list. On a WBB 12 it tracked an external meter
# (r = 0.97, 2026-09) about 80 W below it: the circulation pump and the
# controller are not in it. Older firmware may not serve it at all.
PARAMS_ELECTRICAL_POWER: dict[str, Any] = {
    "only_if_served": True,
    "min": 0,
    "max": 50000,
    "deviceclass": SensorDeviceClass.POWER,
    "precision": 0,
    "unit": UnitOfPower.WATT,
    "stateclass": SensorStateClass.MEASUREMENT,
}

PARAMS_CALCSPREIZUNG: dict = {
    "min": 0,
    "max": 50,
    "divider": 10,
    "operands": ("rl_temp",),
    "deviceclass": SensorDeviceClass.TEMPERATURE,
    "precision": 1,
    "unit": UnitOfTemperature.CELSIUS,
    "stateclass": SensorStateClass.MEASUREMENT,
    "calculation": spread,
}


PARAMS_CALCTAZ: dict = {
    "min": 0,
    "max": 50,
    "operands": ("el_energie_heute",),
    "precision": 2,
    "stateclass": SensorStateClass.MEASUREMENT,
    "calculation": performance_factor,
}

PARAMS_CALCTAZ2: dict = {
    "min": 0,
    "max": 50,
    "operands": ("el_energie_gestern",),
    "precision": 2,
    "calculation": performance_factor,
}

PARAMS_CALCMAZ: dict = {
    "min": 0,
    "max": 50,
    "operands": ("el_energie_monat",),
    "precision": 2,
    "stateclass": SensorStateClass.MEASUREMENT,
    "calculation": performance_factor,
}

PARAMS_CALCJAZ: dict = {
    "min": 0,
    "max": 50,
    "operands": ("el_energie_jahr",),
    "precision": 2,
    "stateclass": SensorStateClass.MEASUREMENT,
    "calculation": performance_factor,
}

PARAMS_PV: dict = {
    "min": 0,
    "max": 65535,
    "precision": 0,
    "deviceclass": SensorDeviceClass.POWER,
    "unit": UnitOfPower.WATT,
    "stateclass": SensorStateClass.MEASUREMENT,
}

PARAMS_TIME_H: dict = {"unit": UnitOfTime.HOURS}


# pylint: disable=line-too-long

##############################################################################################################################
# Modbus Register List:                                                                                                      #
# https://docs.google.com/spreadsheets/d/1EZ3QgyB41xaXo4B5CfZe0Pi8KPwzIGzK/edit?gid=1730751621#gid=1730751621                #
##############################################################################################################################

##############################################################################################################################
# Here are some lists that represent the entities of each device that will be created.
# Every list contains of some ModbusItem objects that have a constructor with the following parameters:
#
# address: The Modbus addres as it is mentioned in the heatpump's documentation
# name:    The entity name. Please note: This entry today only is used to automatically generate translation files.
#          It will be removed in future versions
# mformat: One of the formats defined in FORMATS as they are TEMPERATURE, PERCENTAGE, NUMBER, STATUS or UNKNOWN
#          The format is used to control the conversion of the modbus register entry to the entity variable and back
# mtype:   The type of entity. Currently supported are:
#              SENSOR: A standard sensor entity
#              SENSOR_CALC: A "calculated" sensor. That means, the content of this entity is derived from other entities
#                           of this integration. The definition of the calculation is done in params
#              SELECT: A select entity
#              NUMBER: A number entity. The value of this entity can be changed by the user interface
#              NUMBER_RO: In principle, this is also a number entity that ir writable. But to avoid damages at the heatpump
#                         we decided to make this entity read only.
# device: The devise this entity is assigned to. DEVICES are used here to group the entities in a meaningful way
# params: Parameters to control the behavior of the entity, see description of the params lists
# translation_key: The identifier that points to the right translation key. Therefore, the files strings.json and the
#                  language specific files in the subfolder "translations" have to be up-to-date
##############################################################################################################################

# fmt: off
# --- SYSTEM ITEMS (SYS) ---
MODBUS_SYS_ITEMS: list[ModbusItem] = [
    ModbusItem(address=30001, name="Aussentemperatur", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.SYS, params=PARAMS_STDTEMP, translation_key="aussentemp"),
    ModbusItem(address=30002, name="Luftansaugtemperatur", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.SYS, params=PARAMS_STDTEMP, translation_key="luftansautgemp"),
    ModbusItem(address=30003, name="Fehler", format=FORMATS.STATUS, type=TYPES.SENSOR, device=DEVICES.SYS, resultlist=SYS_FEHLER, translation_key="fehler"),
    ModbusItem(address=30004, name="Warnung", format=FORMATS.STATUS, type=TYPES.SENSOR, device=DEVICES.SYS, resultlist=SYS_FEHLER, translation_key="warnung"),
    ModbusItem(address=30005, name="Fehlerfrei", format=FORMATS.STATUS, type=TYPES.SENSOR, device=DEVICES.SYS, resultlist=SYS_FEHLERFREI, translation_key="fehlerfrei"),
    ModbusItem(address=30006, name="Betriebsanzeige", format=FORMATS.STATUS, type=TYPES.SENSOR, device=DEVICES.SYS, resultlist=SYS_BETRIEBSANZEIGE, translation_key="betriebsanzeige"),
    ModbusItem(address=40001, name="Systembetriebsart", format=FORMATS.STATUS, type=TYPES.SELECT, device=DEVICES.SYS, resultlist=SYS_BETRIEBSART, translation_key="sys_operationmode"),
    ModbusItem(address=40002, name="SollwertPV", format=FORMATS.NUMBER, type=TYPES.NUMBER, device=DEVICES.SYS, params=PARAMS_PV, translation_key="sys_pv"),
]

# --- HEAT PUMP ITEMS (WP) ---
MODBUS_WP_ITEMS: list[ModbusItem] = [
    ModbusItem(address=33101, name="Betrieb", format=FORMATS.STATUS, type=TYPES.SENSOR, device=DEVICES.WP, resultlist=HP_BETRIEB, translation_key="wp_betrieb"),
    ModbusItem(address=33102, name="Störmeldung", format=FORMATS.STATUS, type=TYPES.SENSOR, device=DEVICES.WP, resultlist=HP_STOERMELDUNG, translation_key="wp_stoermeldung"),
    ModbusItem(address=33103, name="Leistungsanforderung", format=FORMATS.PERCENTAGE, type=TYPES.SENSOR, device=DEVICES.WP, params=PARAMS_PERCENTAGE, translation_key="leistungsanforderung"),
    # Calculated Sensor (Calculated downstream, no Modbus block read)
    ModbusItem(address=33103, name="Wärmeleistung", format=FORMATS.NUMBER, type=TYPES.SENSOR_CALC, device=DEVICES.WP, params=PARAMS_CALCPOWER, translation_key="waermeleistung"),
    ModbusItem(address=33104, name="Vorlauftemperatur", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.WP, params=PARAMS_STDTEMP, translation_key="vl_temp"),
    ModbusItem(address=33105, name="Rücklauftemperatur", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.WP, params=PARAMS_STDTEMP, translation_key="rl_temp"),
    ModbusItem(address=33106, name="Verdampfungstemperatur", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.WP, params=PARAMS_STDTEMP, translation_key="verdampfungs_temp"),
    ModbusItem(address=33107, name="Verdichtersauggastemp", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.WP, params=PARAMS_STDTEMP, translation_key="verdichter_ansaug_gas_temp"),
    ModbusItem(address=33108, name="Weichentemperatur", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.WP, params=PARAMS_STDTEMP, translation_key="weichen_temp"),
    ModbusItem(address=33109, name="Anforderung(Vorlauf regenerativ)", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.WP, params=PARAMS_STDTEMP, translation_key="anforderung_vl_regenerativ"),
    ModbusItem(address=33110, name="Puffertemperatur?", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.WP, params=PARAMS_STDTEMP, translation_key="puffer_temp"),
    ModbusItem(address=33111, name="Vorlauftemperatur präzise(Summenvorlauf(B7))", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.WP, params=PARAMS_STDTEMP, translation_key="vl_praeziese_summenvorlauf_b7"),
    # Spread over the condenser: flow B4 (33104) minus return B9; B7 above lags by up to 3 min
    ModbusItem(address=33104, name="Spreizung", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR_CALC, device=DEVICES.WP, params=PARAMS_CALCSPREIZUNG, translation_key="spreizung"),
    ModbusItem(address=33126, name="Elektrische Leistungsaufnahme", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.WP, params=PARAMS_ELECTRICAL_POWER, translation_key="el_leistungsaufnahme"),

    ModbusItem(address=43101, name="Konfiguration", format=FORMATS.STATUS, type=TYPES.NUMBER_RO, device=DEVICES.WP, resultlist=HP_KONFIGURATION, translation_key="wp_konf"),
    ModbusItem(address=43102, name="Ruhemodus", format=FORMATS.STATUS, type=TYPES.NUMBER_RO, device=DEVICES.WP, resultlist=HP_RUHEMODUS, translation_key="ruhemodus"),
    ModbusItem(address=43103, name="Pumpe Einschaltart", format=FORMATS.NUMBER, type=TYPES.NUMBER_RO, device=DEVICES.WP, translation_key="pumpe_einschaltart"),
    ModbusItem(address=43104, name="Sollwert Pumpe Leistung Heizen", format=FORMATS.PERCENTAGE, type=TYPES.NUMBER_RO, device=DEVICES.WP, params=PARAMS_PERCENTAGE, translation_key="sollwert_pumpe_leistung_heizen"),
    ModbusItem(address=43105, name="Sollwert Pumpe Leistung Kühlen", format=FORMATS.PERCENTAGE, type=TYPES.NUMBER_RO, device=DEVICES.WP, params=PARAMS_PERCENTAGE, translation_key="sollwert_pumpe_leistung_kuehlen"),
    ModbusItem(address=43106, name="Sollwert Pumpe Leistung Warmwasser", format=FORMATS.PERCENTAGE, type=TYPES.NUMBER_RO, device=DEVICES.WP, params=PARAMS_PERCENTAGE, translation_key="sollwert_pumpe_leitung_ww"),
    ModbusItem(address=43107, name="Sollwert Pumpe Leistung Abtaubetrieb", format=FORMATS.PERCENTAGE, type=TYPES.NUMBER_RO, device=DEVICES.WP, params=PARAMS_PERCENTAGE, translation_key="sollwert_pumpe_leistung_abtau"),
    ModbusItem(address=43108, name="Sollwert Volumenstrom Heizen", format=FORMATS.NUMBER, type=TYPES.NUMBER_RO, device=DEVICES.WP, params=PARAMS_FLOWRATE, translation_key="soll_volumenstrom_heizen"),
    ModbusItem(address=43109, name="Sollwert Volumenstrom Kühlen", format=FORMATS.NUMBER, type=TYPES.NUMBER_RO, device=DEVICES.WP, params=PARAMS_FLOWRATE, translation_key="soll_volumenstrom_kuehlen"),
    ModbusItem(address=43110, name="Sollwert Volumenstrom Warmwasser", format=FORMATS.NUMBER, type=TYPES.NUMBER_RO, device=DEVICES.WP, params=PARAMS_FLOWRATE, translation_key="soll_volumenstrom_ww"),
]

# --- PRIMARY HEATING CIRCUIT (HZ) ---
MODBUS_HZ_ITEMS = [
    ModbusItem(address=31101, name="Raumsolltemperatur", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.HZ, params={**PARAMS_ROOMTEMP, "setpoint": True}, translation_key="raum_soll_temp"),
    ModbusItem(address=31102, name="Raumtemperatur", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.HZ, params=PARAMS_ROOMTEMP, translation_key="raum_temp"),
    ModbusItem(address=31103, name="Raumfeuchte", format=FORMATS.PERCENTAGE, type=TYPES.SENSOR, device=DEVICES.HZ, params=PARAMS_PERCENTAGE, translation_key="raum_feuchte"),
    ModbusItem(address=31104, name="Vorlaufsolltemperatur", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.HZ, params={**PARAMS_STDTEMP, "setpoint": True}, translation_key="hz_vl_solltemp"),
    ModbusItem(address=31105, name="HZ_Vorlauftemperatur", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.HZ, params=PARAMS_STDTEMP, translation_key="hz_vl_temp"),
    ModbusItem(address=31106, name="Adr. 31106", format=FORMATS.UNKNOWN, type=TYPES.SENSOR, device=DEVICES.HZ, translation_key="adr31106"),

    ModbusItem(address=41101, name="HZ_Konfiguration", format=FORMATS.STATUS, type=TYPES.NUMBER_RO, device=DEVICES.HZ, resultlist=HZ_KONFIGURATION, translation_key="hz_konf"),
    ModbusItem(address=41102, name="Anforderung Typ", format=FORMATS.STATUS, type=TYPES.SELECT, device=DEVICES.HZ, resultlist=HZ_ANFORDERUNG, translation_key="anf_typ"),
    ModbusItem(address=41103, name="Betriebsart", format=FORMATS.STATUS, type=TYPES.SELECT, device=DEVICES.HZ, resultlist=HZ_BETRIEBSART, translation_key="hz_operationmode"),
    ModbusItem(address=41104, name="Pause / Party", format=FORMATS.STATUS, type=TYPES.SELECT, device=DEVICES.HZ, resultlist=HZ_PARTY_PAUSE, translation_key="party_pause"),
    ModbusItem(address=41105, name="Raumsolltemperatur Komfort", format=FORMATS.TEMPERATURE, type=TYPES.NUMBER, device=DEVICES.HZ, params=PARAMS_ROOMTEMP_HIGH, translation_key="raum_soll_temp_komf"),
    ModbusItem(address=41106, name="Raumsolltemperatur Normal", format=FORMATS.TEMPERATURE, type=TYPES.NUMBER, device=DEVICES.HZ, params=PARAMS_ROOMTEMP_MID, translation_key="raum_soll_temp_normal"),
    ModbusItem(address=41107, name="Raumsolltemperatur Absenk", format=FORMATS.TEMPERATURE, type=TYPES.NUMBER, device=DEVICES.HZ, params=PARAMS_ROOMTEMP_LOW, translation_key="raum_soll_temp_absenk"),
    ModbusItem(address=41108, name="Heizkennlinie", format=FORMATS.NUMBER, type=TYPES.NUMBER, device=DEVICES.HZ, params=PARAMS_HZKENNLINIE, translation_key="heizkennlinie"),
    ModbusItem(address=41109, name="Sommer Winter Umschaltung", format=FORMATS.TEMPERATURE, type=TYPES.NUMBER, device=DEVICES.HZ, params=PARAMS_SUMMER_WINTER_SWITCH_TEMP, translation_key="so_wi_umschalt"),
    ModbusItem(address=41110, name="Heizen Konstanttemperatur", format=FORMATS.TEMPERATURE, type=TYPES.NUMBER, device=DEVICES.HZ, params=PARAMS_CONSTANT_FLOW_HEATING, translation_key="heiz_konstanttemp"),
    ModbusItem(address=41111, name="Heizen Konstanttemp Absenk", format=FORMATS.TEMPERATURE, type=TYPES.NUMBER, device=DEVICES.HZ, params=PARAMS_CONSTANT_FLOW_HEATING, translation_key="heiz_konstanttemp_absenk"),
    ModbusItem(address=41112, name="Kühlen Konstanttemperatur", format=FORMATS.TEMPERATURE, type=TYPES.NUMBER, device=DEVICES.HZ, params=PARAMS_CONSTANT_FLOW_COOLING, translation_key="kuehl_konstanttemp"),
]

# --- DYNAMIC ADDITIONAL HEATING CIRCUITS (HZ2 - HZ5) ---
MODBUS_HZ2_ITEMS: list[ModbusItem] = []
for item in MODBUS_HZ_ITEMS:
    mbi2 = copy.deepcopy(x=item)
    if mbi2.address == 41105:
        mbi2.params = PARAMS_ROOMTEMP_HIGH2
    elif mbi2.address == 41106:
        mbi2.params = PARAMS_ROOMTEMP_MID2
    elif mbi2.address == 41107:
        mbi2.params = PARAMS_ROOMTEMP_LOW2
    mbi2.address = item.address + 100
    mbi2.name = item.name + "2"
    mbi2.translation_key = item.translation_key + "2"
    mbi2.device = DEVICES.HZ2
    MODBUS_HZ2_ITEMS.append(mbi2)

MODBUS_HZ3_ITEMS: list[ModbusItem] = []
for item in MODBUS_HZ_ITEMS:
    mbi3 = copy.deepcopy(x=item)
    if mbi3.address == 41105:
        mbi3.params = PARAMS_ROOMTEMP_HIGH3
    elif mbi3.address == 41106:
        mbi3.params = PARAMS_ROOMTEMP_MID3
    elif mbi3.address == 41107:
        mbi3.params = PARAMS_ROOMTEMP_LOW3
    mbi3.address = item.address + 200
    mbi3.name = item.name + "3"
    mbi3.translation_key = item.translation_key + "3"
    mbi3.device = DEVICES.HZ3
    MODBUS_HZ3_ITEMS.append(mbi3)

MODBUS_HZ4_ITEMS: list[ModbusItem] = []
for item in MODBUS_HZ_ITEMS:
    mbi4 = copy.deepcopy(x=item)
    if mbi4.address == 41105:
        mbi4.params = PARAMS_ROOMTEMP_HIGH4
    elif mbi4.address == 41106:
        mbi4.params = PARAMS_ROOMTEMP_MID4
    elif mbi4.address == 41107:
        mbi4.params = PARAMS_ROOMTEMP_LOW4
    mbi4.address = item.address + 300
    mbi4.name = item.name + "4"
    mbi4.translation_key = item.translation_key + "4"
    mbi4.device = DEVICES.HZ4
    MODBUS_HZ4_ITEMS.append(mbi4)

MODBUS_HZ5_ITEMS: list[ModbusItem] = []
for item in MODBUS_HZ_ITEMS:
    mbi5 = copy.deepcopy(x=item)
    if mbi5.address == 41105:
        mbi5.params = PARAMS_ROOMTEMP_HIGH5
    elif mbi5.address == 41106:
        mbi5.params = PARAMS_ROOMTEMP_MID5
    elif mbi5.address == 41107:
        mbi5.params = PARAMS_ROOMTEMP_LOW5
    mbi5.address = item.address + 400
    mbi5.name = item.name + "5"
    mbi5.translation_key = item.translation_key + "5"
    mbi5.device = DEVICES.HZ5
    MODBUS_HZ5_ITEMS.append(mbi5)

# --- HOT WATER ITEMS (WW) ---
MODBUS_WW_ITEMS: list[ModbusItem] = [
    ModbusItem(address=32101, name="Warmwassersolltemperatur", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.WW, params={**PARAMS_WATERTEMP, "setpoint": True}, translation_key="ww_soll_temp"),
    ModbusItem(address=32102, name="Warmwassertemperatur", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.WW, params=PARAMS_WATERTEMP, translation_key="ww_temp"),
    ModbusItem(address=42101, name="WW_Konfiguration", format=FORMATS.STATUS, type=TYPES.NUMBER_RO, device=DEVICES.WW, resultlist=WW_KONFIGURATION, translation_key="ww_konf"),
    ModbusItem(address=42102, name="Warmwasser Push", format=FORMATS.STATUS, type=TYPES.SELECT, device=DEVICES.WW, resultlist=WW_PUSH, translation_key="ww_push"),
    ModbusItem(address=42103, name="Warmwasser Normal", format=FORMATS.TEMPERATURE, type=TYPES.NUMBER, device=DEVICES.WW, params=PARAMS_WATERTEMP_HIGH, translation_key="ww_normal"),
    ModbusItem(address=42104, name="Warmwasser Absenk", format=FORMATS.TEMPERATURE, type=TYPES.NUMBER, device=DEVICES.WW, params=PARAMS_WATERTEMP_LOW, translation_key="ww_absenk"),
    ModbusItem(address=42105, name="SG Ready Anhebung", format=FORMATS.TEMPERATURE, type=TYPES.NUMBER, device=DEVICES.WW, params=PARAMS_SGREADYTEMP, translation_key="sgr_anhebung"),
]

# --- SECONDARY HEAT GENERATOR (W2) ---
# 34102/34103/34106 follow the Weishaupt register list and a pump running
# 2025 firmware; the labels the upstream table gave them belonged to other
# registers. Renaming one of these is an entry migration (see __init__.py).
MODBUS_W2_ITEMS: list[ModbusItem] = [
    ModbusItem(address=34101, name="Status 2. WEZ", format=FORMATS.STATUS, type=TYPES.SENSOR, device=DEVICES.W2, resultlist=W2_STATUS, translation_key="status_2_wez"),
    ModbusItem(address=34102, name="Betriebsstunden 2. WEZ", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.W2, params=PARAMS_TIME_H, translation_key="betriebss_2wez"),
    ModbusItem(address=34103, name="Schaltspiele 2. WEZ", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.W2, translation_key="schaltsp_2wez"),
    ModbusItem(address=34104, name="Status E-Heizung 1", format=FORMATS.STATUS, type=TYPES.SENSOR, device=DEVICES.W2, resultlist=W2_STATUS, translation_key="status_e1"),
    ModbusItem(address=34105, name="Status E-Heizung 2", format=FORMATS.STATUS, type=TYPES.SENSOR, device=DEVICES.W2, resultlist=W2_STATUS, translation_key="status_e2"),
    ModbusItem(address=34106, name="Betriebsstunden E1", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.W2, params=PARAMS_TIME_H, translation_key="betriebss_e1"),
    ModbusItem(address=34107, name="Betriebsstunden E2", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.W2, params=PARAMS_TIME_H, translation_key="betriebss_e2"),
    ModbusItem(address=44101, name="W2_Konfiguration", format=FORMATS.STATUS, type=TYPES.NUMBER_RO, device=DEVICES.W2, resultlist=W2_KONFIG, translation_key="w2_konf"),
    ModbusItem(address=44102, name="Konfiguration EP1", format=FORMATS.STATUS, type=TYPES.NUMBER_RO, device=DEVICES.W2, resultlist=EP1_KONFIG, translation_key="adr44102"),
    ModbusItem(address=44103, name="Konfiguration EP2", format=FORMATS.STATUS, type=TYPES.NUMBER_RO, device=DEVICES.W2, resultlist=EP2_KONFIG, translation_key="adr44103"),
    ModbusItem(address=44104, name="Grenztemperatur", format=FORMATS.TEMPERATURE, type=TYPES.NUMBER, device=DEVICES.W2, params=PARAMS_BIVALENZTEMP, translation_key="grenztemp"),
    ModbusItem(address=44105, name="Bivalenztemperatur", format=FORMATS.TEMPERATURE, type=TYPES.NUMBER, device=DEVICES.W2, params=PARAMS_BIVALENZTEMP, translation_key="bivalenztemp"),
    ModbusItem(address=44106, name="Bivalenztemperatur WW", format=FORMATS.TEMPERATURE, type=TYPES.NUMBER, device=DEVICES.W2, params=PARAMS_BIVALENZTEMP, translation_key="bivalenztemp_ww"),
]

# --- STATISTICS & ENERGY ITEMS (ST) ---
MODBUS_ST_ITEMS: list[ModbusItem] = [
    ModbusItem(address=36101, name="Gesamt Energie heute", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="ges_energie_heute"),
    # Calculated Sensor (Calculated downstream, no Modbus block read)
    ModbusItem(address=36101, name="Tagesarbeitszahl heute", format=FORMATS.NUMBER, type=TYPES.SENSOR_CALC, device=DEVICES.ST, params=PARAMS_CALCTAZ, translation_key="tagesarbeitszahl_heute"),
    ModbusItem(address=36102, name="Gesamt Energie gestern", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY_YESTERDAY, translation_key="ges_energie_yesterday"),
    # Calculated Sensor (Calculated downstream, no Modbus block read)
    ModbusItem(address=36102, name="Tagesarbeitszahl gestern", format=FORMATS.NUMBER, type=TYPES.SENSOR_CALC, device=DEVICES.ST, params=PARAMS_CALCTAZ2, translation_key="tagesarbeitszahl_gestern"),
    ModbusItem(address=36103, name="Gesamt Energie Monat", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="ges_energie_monat"),
    # Calculated Sensor (Calculated downstream, no Modbus block read)
    ModbusItem(address=36103, name="Monatsarbeitszahl", format=FORMATS.NUMBER, type=TYPES.SENSOR_CALC, device=DEVICES.ST, params=PARAMS_CALCMAZ, translation_key="monatsarbeitszahl"),
    # The "Jahr" rows answer but read 0 on every controller seen (live 2026-09,
    # a pump in service for years; the Evoblock scan in upstream #193): the
    # yearly total is on the display and in the portal only.
    ModbusItem(address=36104, name="Gesamt Energie Jahr", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="ges_energie_jahr"),
    # Calculated Sensor (Calculated downstream, no Modbus block read)
    ModbusItem(address=36104, name="Jahresarbeitszahl", format=FORMATS.NUMBER, type=TYPES.SENSOR_CALC, device=DEVICES.ST, params=PARAMS_CALCJAZ, translation_key="jahresarbeitszahl"),

    ModbusItem(address=36201, name="Heizen Energie heute", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="heiz_energie_heute"),
    ModbusItem(address=36202, name="Heizen Energie gestern", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY_YESTERDAY, translation_key="heiz_energie_getern"),
    ModbusItem(address=36203, name="Heizen Energie Monat", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="heiz_energie_monat"),
    ModbusItem(address=36204, name="Heizen Energie Jahr", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="heiz_energie_jahr"),

    ModbusItem(address=36301, name="Warmwasser Energie heute", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="ww_energie_heute"),
    ModbusItem(address=36302, name="Warmwasser Energie gestern", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY_YESTERDAY, translation_key="ww_energie_gestern"),
    ModbusItem(address=36303, name="Warmwasser Energie Monat", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="ww_energie_monat"),
    ModbusItem(address=36304, name="Warmwasser Energie Jahr", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="ww_energie_jahr"),

    ModbusItem(address=36401, name="Kühlen Energie heute", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="kuehl_energie_heute"),
    ModbusItem(address=36402, name="Kühlen Energie gestern", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY_YESTERDAY, translation_key="kuehl_energie_gestern"),
    ModbusItem(address=36403, name="Kühlen Energie Monat", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="kuehl_energie_monat"),
    ModbusItem(address=36404, name="Kühlen Energie Jahr", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="kuehl_energie_jahr"),

    ModbusItem(address=36501, name="Abtauen Energie heute", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="abtau_energie_heute"),
    ModbusItem(address=36502, name="Abtauen Energie gestern", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY_YESTERDAY, translation_key="abtau_energie_gester"),
    ModbusItem(address=36503, name="Abtauen Energie Monat", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="abtau_energie_monat"),
    ModbusItem(address=36504, name="Abtauen Energie Jahr", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="abtau_energie_jahr"),

    ModbusItem(address=36601, name="Gesamt Energie II heute", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="ges_energie_today"),
    ModbusItem(address=36602, name="Gesamt Energie II gestern", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY_YESTERDAY, translation_key="ges_energie_2_yesterday"),
    ModbusItem(address=36603, name="Gesamt Energie II Monat", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="ges_energie_2_monat"),
    ModbusItem(address=36604, name="Gesamt Energie II Jahr", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="ges_energie_year"),

    ModbusItem(address=36701, name="Elektr. Energie heute", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="el_energie_heute"),
    ModbusItem(address=36702, name="Elektr. Energie gestern", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY_YESTERDAY, translation_key="el_energie_gestern"),
    ModbusItem(address=36703, name="Elektr. Energie Monat", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="el_energie_monat"),
    ModbusItem(address=36704, name="Elektr. Energie Jahr", format=FORMATS.NUMBER, type=TYPES.SENSOR, device=DEVICES.ST, params=PARAMS_ENERGY, translation_key="el_energie_jahr"),
    ModbusItem(address=36801, name="Adr. 36801", format=FORMATS.UNKNOWN, type=TYPES.SENSOR, device=DEVICES.ST, translation_key="adr36801"),
]

# --- INPUT / OUTPUT ITEMS (IO) ---
# H1.2-H1.5 are "FormatSensor" in the 2025 data-point list: a temperature
# input, or the digital status words 0x800A/0x800B when used as a switch
# input (then no reading).
MODBUS_IO_ITEMS: list[ModbusItem] = [
    ModbusItem(address=35101, name="SG-Ready 1", format=FORMATS.STATUS, type=TYPES.SENSOR, device=DEVICES.IO, resultlist=W2_STATUS, translation_key="sgr1"),
    ModbusItem(address=35102, name="SG-Ready 2", format=FORMATS.STATUS, type=TYPES.SENSOR, device=DEVICES.IO, resultlist=W2_STATUS, translation_key="sgr2"),
    ModbusItem(address=35103, name="Ausgang H1.2", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.IO, params=PARAMS_STDTEMP, translation_key="ausg_h12"),
    ModbusItem(address=35104, name="Ausgang H1.3", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.IO, params=PARAMS_STDTEMP, translation_key="ausg_h13"),
    ModbusItem(address=35105, name="Ausgang H1.4", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.IO, params=PARAMS_STDTEMP, translation_key="ausg_h14"),
    ModbusItem(address=35106, name="Ausgang H1.5", format=FORMATS.TEMPERATURE, type=TYPES.SENSOR, device=DEVICES.IO, params=PARAMS_STDTEMP, translation_key="ausg_h15"),
    ModbusItem(address=35107, name="Eingang DE1", format=FORMATS.STATUS, type=TYPES.SENSOR, device=DEVICES.IO, resultlist=W2_STATUS, translation_key="eing_de1"),
    ModbusItem(address=35108, name="Eingang DE2", format=FORMATS.STATUS, type=TYPES.SENSOR, device=DEVICES.IO, resultlist=W2_STATUS, translation_key="eing_de2"),

    ModbusItem(address=45101, name="Konf. Eingang SGR1", format=FORMATS.STATUS, type=TYPES.NUMBER_RO, device=DEVICES.IO, resultlist=IO_KONFIG_IN, translation_key="konf_eing_sgr1"),
    ModbusItem(address=45102, name="Konf. Eingang SGR2", format=FORMATS.STATUS, type=TYPES.NUMBER_RO, device=DEVICES.IO, resultlist=IO_KONFIG_IN, translation_key="konf_eing_sgr2"),
    ModbusItem(address=45103, name="Konf. Ausgang H1.2", format=FORMATS.STATUS, type=TYPES.NUMBER_RO, device=DEVICES.IO, resultlist=IO_KONFIG, translation_key="konf_ausg_h12"),
    ModbusItem(address=45104, name="Konf. Ausgang  H1.3", format=FORMATS.STATUS, type=TYPES.NUMBER_RO, device=DEVICES.IO, resultlist=IO_KONFIG, translation_key="konf_ausg_h13"),
    ModbusItem(address=45105, name="Konf. Ausgang  H1.4", format=FORMATS.STATUS, type=TYPES.NUMBER_RO, device=DEVICES.IO, resultlist=IO_KONFIG, translation_key="konf_ausg_h14"),
    ModbusItem(address=45106, name="Konf. Ausgang  H1.5", format=FORMATS.STATUS, type=TYPES.NUMBER_RO, device=DEVICES.IO, resultlist=IO_KONFIG, translation_key="konf_ausg_h15"),
    ModbusItem(address=45107, name="Konf. Eingang DE1", format=FORMATS.STATUS, type=TYPES.NUMBER_RO, device=DEVICES.IO, resultlist=IO_KONFIG_IN, translation_key="konf_eing_de1"),
    ModbusItem(address=45108, name="Konf. Eingang DE2", format=FORMATS.STATUS, type=TYPES.NUMBER_RO, device=DEVICES.IO, resultlist=IO_KONFIG_IN, translation_key="konf_eing_de2"),
]


DEVICELISTS: list = [
    MODBUS_SYS_ITEMS,
    MODBUS_WP_ITEMS,
    MODBUS_WW_ITEMS,
    MODBUS_HZ_ITEMS,
    MODBUS_HZ2_ITEMS,
    MODBUS_HZ3_ITEMS,
    MODBUS_HZ4_ITEMS,
    MODBUS_HZ5_ITEMS,
    MODBUS_W2_ITEMS,
    MODBUS_ST_ITEMS,
    MODBUS_IO_ITEMS
]

# fmt: on
