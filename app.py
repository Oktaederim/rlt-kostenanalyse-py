import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import plotly.graph_objects as go
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

# Seiten-Konfiguration
st.set_page_config(
    page_title="RLT-Anlagen Kostenanalyse Professional",
    page_icon="🏭",
    layout="wide"
)

def saettigungsdampfdruck(temp):
    """Sättigungsdampfdruck nach Magnus-Formel [Pa]"""
    return 611.2 * np.exp((17.67 * temp) / (temp + 243.5))

def abs_feuchte_aus_rel_feuchte(temp, rel_feuchte):
    """Absolute Feuchte aus relativer Feuchte [kg/kg]"""
    es = saettigungsdampfdruck(temp)
    e = rel_feuchte / 100 * es
    return 0.622 * e / (101325 - e)

def rel_feuchte_aus_abs_feuchte(temp, abs_feuchte):
    """Relative Feuchte aus absoluter Feuchte [%]"""
    es = saettigungsdampfdruck(temp)
    e = abs_feuchte * 101325 / (0.622 + abs_feuchte)
    return min(100, e / es * 100)

def enthalpie_feuchte_luft(temp, abs_feuchte):
    """Enthalpie feuchter Luft [J/kg]"""
    return 1005 * temp + abs_feuchte * (2501000 + 1870 * temp)

def taupunkt_aus_abs_feuchte(abs_feuchte):
    """Taupunkt aus absoluter Feuchte [°C]"""
    if abs_feuchte <= 0:
        return -50
    e = abs_feuchte * 101325 / (0.622 + abs_feuchte)
    return 243.5 * np.log(e/611.2) / (17.67 - np.log(e/611.2))

def kuhltemperatur_fur_entfeuchtung(ziel_abs_feuchte, sicherheit=1):
    """Erforderliche Kühltemperatur für Entfeuchtung [°C]"""
    taupunkt = taupunkt_aus_abs_feuchte(ziel_abs_feuchte)
    return taupunkt - sicherheit

def berechne_luftzustand(temp, rel_feuchte=None, abs_feuchte=None):
    """Vollständiger Luftzustand"""
    if abs_feuchte is None:
        abs_feuchte = abs_feuchte_aus_rel_feuchte(temp, rel_feuchte)
    else:
        rel_feuchte = rel_feuchte_aus_abs_feuchte(temp, abs_feuchte)
    
    return {
        'temperatur': round(temp, 1),
        'rel_feuchte': round(rel_feuchte, 1),
        'abs_feuchte': round(abs_feuchte * 1000, 2),
        'enthalpie': round(enthalpie_feuchte_luft(temp, abs_feuchte) / 1000, 1),
        'taupunkt': round(taupunkt_aus_abs_feuchte(abs_feuchte), 1)
    }

def berechne_rlt_prozess(temp_aussen, feuchte_aussen, temp_zuluft, feuchte_zuluft_soll, wrg_grad, entfeuchten):
    """Professionelle RLT-Prozessberechnung"""
    prozess = {
        'schritte': [],
        'leistungen': {
            'wrg_rueckgewinn': 0,
            'kuehlung_entf': 0,
            'nachheizung': 0,
            'heizung_direkt': 0,
            'kuehlung_direkt': 0
        }
    }
    
    # Schritt 1: Außenluft
    abs_feuchte_aussen = abs_feuchte_aus_rel_feuchte(temp_aussen, feuchte_aussen)
    zustand_aussen = berechne_luftzustand(temp_aussen, feuchte_aussen)
    zustand_aussen['punkt'] = 'Außenluft'
    prozess['schritte'].append(zustand_aussen)
    
    aktueller_zustand = {
        'temp': temp_aussen,
        'abs_feuchte': abs_feuchte_aussen,
        'enthalpie': enthalpie_feuchte_luft(temp_aussen, abs_feuchte_aussen)
    }
    
    # Schritt 2: Wärmerückgewinnung
    if wrg_grad > 0:
        temp_nach_wrg = temp_aussen + (temp_zuluft - temp_aussen) * wrg_grad
        zustand_wrg = berechne_luftzustand(temp_nach_wrg, abs_feuchte=abs_feuchte_aussen)
        zustand_wrg['punkt'] = 'Nach WRG'
        prozess['schritte'].append(zustand_wrg)
        
        prozess['leistungen']['wrg_rueckgewinn'] = aktueller_zustand['enthalpie'] - enthalpie_feuchte_luft(temp_nach_wrg, abs_feuchte_aussen)
        
        aktueller_zustand['temp'] = temp_nach_wrg
        aktueller_zustand['enthalpie'] = enthalpie_feuchte_luft(temp_nach_wrg, abs_feuchte_aussen)
    
    # Schritt 3: Entfeuchtung
    ziel_abs_feuchte = abs_feuchte_aus_rel_feuchte(temp_zuluft, feuchte_zuluft_soll)
    
    if entfeuchten and aktueller_zustand['abs_feuchte'] > ziel_abs_feuchte:
        temp_kuehlung = kuhltemperatur_fur_entfeuchtung(ziel_abs_feuchte)
        
        zustand_gekuehlt = berechne_luftzustand(temp_kuehlung, abs_feuchte=ziel_abs_feuchte)
        zustand_gekuehlt['punkt'] = f'Gekühlt/Entfeuchtet auf {temp_kuehlung:.1f}°C'
        prozess['schritte'].append(zustand_gekuehlt)
        
        enthalpie_gekuehlt = enthalpie_feuchte_luft(temp_kuehlung, ziel_abs_feuchte)
        prozess['leistungen']['kuehlung_entf'] = aktueller_zustand['enthalpie'] - enthalpie_gekuehlt
        
        aktueller_zustand['temp'] = temp_kuehlung
        aktueller_zustand['abs_feuchte'] = ziel_abs_feuchte
        aktueller_zustand['enthalpie'] = enthalpie_gekuehlt
        
        # Schritt 4: Nachheizung
        if temp_kuehlung < temp_zuluft:
            zustand_nachgeheizt = berechne_luftzustand(temp_zuluft, abs_feuchte=ziel_abs_feuchte)
            zustand_nachgeheizt['punkt'] = 'Nachheizung'
            prozess['schritte'].append(zustand_nachgeheizt)
            
            enthalpie_final = enthalpie_feuchte_luft(temp_zuluft, ziel_abs_feuchte)
            prozess['leistungen']['nachheizung'] = enthalpie_final - aktueller_zustand['enthalpie']
    
    else:
        # Keine Entfeuchtung - direkte Temperierung
        if aktueller_zustand['temp'] < temp_zuluft:
            zustand_final = berechne_luftzustand(temp_zuluft, abs_feuchte=aktueller_zustand['abs_feuchte'])
            zustand_final['punkt'] = 'Erwärmung'
            prozess['schritte'].append(zustand_final)
            
            enthalpie_final = enthalpie_feuchte_luft(temp_zuluft, aktueller_zustand['abs_feuchte'])
            prozess['leistungen']['heizung_direkt'] = enthalpie_final - aktueller_zustand['enthalpie']
            
        elif aktueller_zustand['temp'] > temp_zuluft:
            zustand_final = berechne_luftzustand(temp_zuluft, abs_feuchte=aktueller_zustand['abs_feuchte'])
            zustand_final['punkt'] = 'Kühlung'
            prozess['schritte'].append(zustand_final)
            
            enthalpie_final = enthalpie_feuchte_luft(temp_zuluft, aktueller_zustand['abs_feuchte'])
            prozess['leistungen']['kuehlung_direkt'] = aktueller_zustand['enthalpie'] - enthalpie_final
    
    return prozess

def berechne_jahresstunden(betrieb_normal, betrieb_abgesenkt, wartung_tage, ferien_tage):
    """Berechnet effektive Jahresstunden"""
    verfugbare_tage = 365 - wartung_tage - ferien_tage
    
    normal_stunden = betrieb_normal['tage_woche'] * betrieb_normal['stunden_tag'] * 52
    abgesenkt_stunden = betrieb_abgesenkt['tage_woche'] * betrieb_abgesenkt['stunden_tag'] * 52
    
    # Begrenzung auf verfügbare Tage
    gesamt_stunden_woche = (betrieb_normal['tage_woche'] + betrieb_abgesenkt['tage_woche']) * 52
    max_stunden = verfugbare_tage * 24
    
    faktor = min(1.0, max_stunden / max(1, gesamt_stunden_woche * 24 / 7))
    
    return {
        'normal': normal_stunden * faktor,
        'abgesenkt': abgesenkt_stunden * faktor,
        'gesamt': (normal_stunden + abgesenkt_stunden) * faktor,
        'verfugbare_tage': verfugbare_tage
    }

# Haupttitel
st.title("🏭 RLT-Anlagen Kostenanalyse Professional")
st.markdown("*Professionelle Kostenberechnung mit korrekter Thermodynamik*")
st.markdown("---")

# Haupteingabebereich
col_left, col_right = st.columns([1, 2])

with col_left:
    st.header("⚙️ Konfiguration")
    
    # 1. ANLAGENPARAMETER
    with st.expander("📋 Anlagenparameter", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            volumenstrom_slider = st.slider("Volumenstrom [m³/h]", 100, 500000, 10000, step=500)
        with col2:
            volumenstrom_input = st.number_input("Exakte Eingabe", value=volumenstrom_slider, min_value=100, max_value=500000)
        
        volumenstrom = volumenstrom_input if volumenstrom_input != volumenstrom_slider else volumenstrom_slider
        
        col1, col2 = st.columns(2)
        with col1:
            sfp_slider = st.slider("SFP [W/(m³/h)]", 0.5, 6.0, 2.5, step=0.1)
        with col2:
            sfp_input = st.number_input("SFP exakt", value=sfp_slider, min_value=0.5, max_value=6.0, step=0.1)
        
        sfp = sfp_input if sfp_input != sfp_slider else sfp_slider
        
        teillast_faktor = st.slider("Durchschnittliche Teillast [%]", 30, 100, 80) / 100
    
    # 2. BETRIEBSMODI
    with st.expander("🔧 Betriebsmodi", expanded=True):
        entfeuchten = st.checkbox("🌊 Entfeuchtung aktiv", False)
        
        st.markdown("**Wärmerückgewinnung:**")
        wrg_vorhanden = st.checkbox("WRG vorhanden", True)
        if wrg_vorhanden:
            col1, col2 = st.columns(2)
            with col1:
                wrg_slider = st.slider("WRG-Wirkungsgrad [%]", 50, 95, 70)
            with col2:
                wrg_input = st.number_input("WRG exakt [%]", value=wrg_slider, min_value=50, max_value=95)
            wrg_wirkungsgrad = (wrg_input if wrg_input != wrg_slider else wrg_slider) / 100
        else:
            wrg_wirkungsgrad = 0
    
    # 3. BETRIEBSZEITEN
    with st.expander("⏰ Betriebszeiten", expanded=True):
        st.markdown("**Normalbetrieb:**")
        col1, col2 = st.columns(2)
        with col1:
            normal_tage = st.slider("Tage/Woche", 1, 7, 5, key="normal_tage")
            normal_stunden = st.slider("Stunden/Tag", 1, 24, 12, key="normal_stunden")
        with col2:
            st.metric("Stunden/Woche", f"{normal_tage * normal_stunden}")
            st.metric("Stunden/Jahr", f"{normal_tage * normal_stunden * 52:,.0f}")
        
        st.markdown("**Absenkbetrieb:**")
        absenk_aktiv = st.checkbox("Absenkbetrieb aktivieren", True)
        if absenk_aktiv:
            col1, col2 = st.columns(2)
            with col1:
                absenk_tage = st.slider("Tage/Woche", 0, 7, 2, key="absenk_tage")
                absenk_stunden = st.slider("Stunden/Tag", 1, 24, 8, key="absenk_stunden")
                absenk_reduktion = st.slider("Leistungsreduktion [%]", 10, 70, 50) / 100
            with col2:
                st.metric("Absenkstunden/Jahr", f"{absenk_tage * absenk_stunden * 52:,.0f}")
                st.info(f"Reduktion: {absenk_reduktion*100:.0f}%")
        else:
            absenk_tage = absenk_stunden = 0
            absenk_reduktion = 0
        
        st.markdown("**Ausfallzeiten:**")
        col1, col2 = st.columns(2)
        with col1:
            wartung_tage = st.slider("Wartung [Tage/Jahr]", 0, 30, 5)
        with col2:
            ferien_tage = st.slider("Betriebsferien [Tage/Jahr]", 0, 60, 14)

    # 4. AUßENLUFTBEDINGUNGEN
    with st.expander("🌤️ Außenluftbedingungen", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            temp_aussen_slider = st.slider("Außentemperatur [°C]", -20, 45, 5)
        with col2:
            temp_aussen_input = st.number_input("Exakte Temp. [°C]", value=float(temp_aussen_slider), min_value=-20.0, max_value=45.0, step=0.5)
        temp_aussen = temp_aussen_input if temp_aussen_input != temp_aussen_slider else temp_aussen_slider
        
        col1, col2 = st.columns(2)
        with col1:
            feuchte_aussen_slider = st.slider("Außenluft rel. Feuchte [%]", 20, 95, 60)
        with col2:
            feuchte_aussen_input = st.number_input("Exakte Feuchte [%]", value=feuchte_aussen_slider, min_value=20, max_value=95)
        feuchte_aussen = feuchte_aussen_input if feuchte_aussen_input != feuchte_aussen_slider else feuchte_aussen_slider

    # 5. ZULUFTBEDINGUNGEN
    with st.expander("🎯 Zuluftbedingungen", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            temp_zuluft_slider = st.slider("Zuluft-Solltemperatur [°C]", 16, 30, 20)
        with col2:
            temp_zuluft_input = st.number_input("Exakte Zuluft-Temp. [°C]", value=float(temp_zuluft_slider), min_value=16.0, max_value=30.0, step=0.5)
        temp_zuluft = temp_zuluft_input if temp_zuluft_input != temp_zuluft_slider else temp_zuluft_slider
        
        if entfeuchten:
            feuchte_modus = st.radio("Feuchte-Eingabe:", ["Relative Feuchte [%]", "Absolute Feuchte [g/kg]"])
            
            if feuchte_modus == "Relative Feuchte [%]":
                col1, col2 = st.columns(2)
                with col1:
                    feuchte_zuluft_slider = st.slider("Zuluft rel. Feuchte [%]", 30, 70, 45)
                with col2:
                    feuchte_zuluft_input = st.number_input("Exakte rel. Feuchte [%]", value=feuchte_zuluft_slider, min_value=30, max_value=70)
                feuchte_zuluft_soll = feuchte_zuluft_input if feuchte_zuluft_input != feuchte_zuluft_slider else feuchte_zuluft_slider
                
                # Zeige entsprechende absolute Feuchte
                abs_feuchte_berechnet = abs_feuchte_aus_rel_feuchte(temp_zuluft, feuchte_zuluft_soll) * 1000
                st.info(f"💡 Entspricht {abs_feuchte_berechnet:.2f} g/kg absoluter Feuchte")
            
            else:  # Absolute Feuchte
                col1, col2 = st.columns(2)
                with col1:
                    abs_feuchte_slider = st.slider("Zuluft abs. Feuchte [g/kg]", 5.0, 15.0, 8.0, step=0.1)
                with col2:
                    abs_feuchte_input = st.number_input("Exakte abs. Feuchte [g/kg]", value=abs_feuchte_slider, min_value=5.0, max_value=15.0, step=0.1)
                abs_feuchte_ziel = (abs_feuchte_input if abs_feuchte_input != abs_feuchte_slider else abs_feuchte_slider) / 1000
                
                feuchte_zuluft_soll = rel_feuchte_aus_abs_feuchte(temp_zuluft, abs_feuchte_ziel)
                st.info(f"💡 Entspricht {feuchte_zuluft_soll:.1f}% rel. Feuchte bei {temp_zuluft}°C")
        else:
            feuchte_zuluft_soll = feuchte_aussen
    
    # 6. ENERGIEPREISE
    with st.expander("💰 Energiepreise", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            preis_strom = st.number_input("Strom [€/kWh]", value=0.25, format="%.3f", step=0.01)
            preis_waerme = st.number_input("Fernwärme [€/kWh]", value=0.08, format="%.3f", step=0.01)
        with col2:
            preis_kaelte = st.number_input("Kälte [€/kWh]", value=0.15, format="%.3f", step=0.01)

with col_right:
    st.header("📊 Ergebnisse")
    
    # Berechnungen durchführen
    prozess_normal = berechne_rlt_prozess(temp_aussen, feuchte_aussen, temp_zuluft, feuchte_zuluft_soll, wrg_wirkungsgrad, entfeuchten)
    prozess_abgesenkt = berechne_rlt_prozess(temp_aussen, feuchte_aussen, temp_zuluft - 3, feuchte_zuluft_soll, wrg_wirkungsgrad, entfeuchten) if absenk_aktiv else prozess_normal
    
    # Betriebsstunden berechnen
    betrieb_normal = {'tage_woche': normal_tage, 'stunden_tag': normal_stunden}
    betrieb_abgesenkt = {'tage_woche': absenk_tage, 'stunden_tag': absenk_stunden}
    jahresstunden = berechne_jahresstunden(betrieb_normal, betrieb_abgesenkt, wartung_tage, ferien_tage)
    
    # Tab-Struktur für Ergebnisse
    tab1, tab2, tab3 = st.tabs(["⚡ Leistungen & Kosten", "🔄 Luftbehandlungsprozess", "📈 Diagramme"])
    
    with tab1:
        # Leistungsberechnungen
        luftmassenstrom = volumenstrom * 1.2 / 3600  # kg/s
        
        # Ventilatorleistung
        p_ventilator = volumenstrom * sfp / 1000  # kW
        
        # Thermische Leistungen Normalbetrieb
        p_kuehlung_entf_normal = luftmassenstrom * prozess_normal['leistungen']['kuehlung_entf'] / 1000
        p_nachheizung_normal = luftmassenstrom * prozess_normal['leistungen']['nachheizung'] / 1000
        p_heizung_direkt_normal = luftmassenstrom * prozess_normal['leistungen']['heizung_direkt'] / 1000
        p_kuehlung_direkt_normal = luftmassenstrom * prozess_normal['leistungen']['kuehlung_direkt'] / 1000
        
        # Thermische Leistungen Absenkbetrieb
        p_kuehlung_entf_abgesenkt = luftmassenstrom * prozess_abgesenkt['leistungen']['kuehlung_entf'] / 1000 * absenk_reduktion
        p_nachheizung_abgesenkt = luftmassenstrom * prozess_abgesenkt['leistungen']['nachheizung'] / 1000 * absenk_reduktion
        p_heizung_direkt_abgesenkt = luftmassenstrom * prozess_abgesenkt['leistungen']['heizung_direkt'] / 1000 * absenk_reduktion
        p_kuehlung_direkt_abgesenkt = luftmassenstrom * prozess_abgesenkt['leistungen']['kuehlung_direkt'] / 1000 * absenk_reduktion
        
        # Gesamte Heiz- und Kühlleistungen
        p_heizen_normal = p_nachheizung_normal + p_heizung_direkt_normal
        p_kuehlen_normal = p_kuehlung_entf_normal + p_kuehlung_direkt_normal
        p_heizen_abgesenkt = p_nachheizung_abgesenkt + p_heizung_direkt_abgesenkt
        p_kuehlen_abgesenkt = p_kuehlung_entf_abgesenkt + p_kuehlung_direkt_abgesenkt
        
        # Jahreskosten berechnen
        kosten_ventilator = p_ventilator * teillast_faktor * jahresstunden['gesamt'] * preis_strom
        kosten_heizen = (p_heizen_normal * jahresstunden['normal'] + p_heizen_abgesenkt * jahresstunden['abgesenkt']) * preis_waerme
        kosten_kuehlen = (p_kuehlen_normal * jahresstunden['normal'] + p_kuehlen_abgesenkt * jahresstunden['abgesenkt']) * preis_kaelte
        
        # Ergebnisse-Dashboard
        st.subheader("⚡ Installierte Leistungen")
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric(
                "🌀 Ventilator",
                f"{p_ventilator:.1f} kW",
                f"bei {teillast_faktor*100:.0f}% Teillast: {p_ventilator*teillast_faktor:.1f} kW"
            )
        
        with col2:
            st.metric(
                "🔥 Heizung",
                f"{p_heizen_normal:.1f} kW",
                f"Nachheizung: {p_nachheizung_normal:.1f} kW" if p_nachheizung_normal > 0 else None
            )
        
        with col3:
            st.metric(
                "❄️ Kühlung",
                f"{p_kuehlen_normal:.1f} kW",
                f"Entfeuchtung: {p_kuehlung_entf_normal:.1f} kW" if p_kuehlung_entf_normal > 0 else None
            )
        
        with col4:
            p_gesamt = p_ventilator + p_heizen_normal + p_kuehlen_normal
            st.metric(
                "⚡ Gesamtleistung",
                f"{p_gesamt:.1f} kW",
                f"Anschlusswert"
            )
        
        st.markdown("---")
        st.subheader("💰 Jahreskosten")
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric(
                "🌀 Ventilator",
                f"{kosten_ventilator:.0f} €/a",
                f"{kosten_ventilator/12:.0f} €/Monat"
            )
        
        with col2:
            st.metric(
                "🔥 Heizung",
                f"{kosten_heizen:.0f} €/a",
                f"{kosten_heizen/12:.0f} €/Monat"
            )
        
        with col3:
            st.metric(
                "❄️ Kühlung",
                f"{kosten_kuehlen:.0f} €/a",
                f"{kosten_kuehlen/12:.0f} €/Monat"
            )
        
        with col4:
            gesamtkosten = kosten_ventilator + kosten_heizen + kosten_kuehlen
            st.metric(
                "💯 Gesamtkosten",
                f"{gesamtkosten:.0f} €/a",
                f"{gesamtkosten/12:.0f} €/Monat"
            )
        
        # Betriebszeiten-Übersicht
        st.markdown("---")
        st.subheader("⏰ Betriebszeiten-Übersicht")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Normalbetrieb", f"{jahresstunden['normal']:,.0f} h/a")
        with col2:
            st.metric("Absenkbetrieb", f"{jahresstunden['abgesenkt']:,.0f} h/a" if absenk_aktiv else "0 h/a")
        with col3:
            st.metric("Verfügbare Tage", f"{jahresstunden['verfugbare_tage']} Tage/a")
    
    with tab2:
        st.subheader("🔄 Luftbehandlungsprozess (h,x-Diagramm)")
        
        # Tabelle mit Spaltenüberschriften
        df_prozess = pd.DataFrame(prozess_normal['schritte'])
        
        # Spalten umbenennen für bessere Lesbarkeit
        df_display = df_prozess.copy()
        df_display.columns = ['Prozessschritt', 'Temperatur [°C]', 'rel. Feuchte [%]', 'abs. Feuchte [g/kg]', 'Enthalpie [kJ/kg]', 'Taupunkt [°C]']
        
        st.dataframe(df_display, use_container_width=True, hide_index=True)
        
        st.markdown("---")
        st.subheader("🔧 Prozessleistungen")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**Kühlleistungen:**")
            if p_kuehlung_entf_normal > 0:
                st.success(f"❄️ Kühlung für Entfeuchtung: **{p_kuehlung_entf_normal:.1f} kW**")
            if p_kuehlung_direkt_normal > 0:
                st.info(f"❄️ Direkte Kühlung: **{p_kuehlung_direkt_normal:.1f} kW**")
            if p_kuehlung_entf_normal == 0 and p_kuehlung_direkt_normal == 0:
                st.info("Keine Kühlung erforderlich")
        
        with col2:
           st.markdown("**Heizleistungen:**")
           if p_nachheizung_normal > 0:
               st.warning(f"🔥 Nachheizung: **{p_nachheizung_normal:.1f} kW**")
           if p_heizung_direkt_normal > 0:
               st.warning(f"🔥 Direkte Heizung: **{p_heizung_direkt_normal:.1f} kW**")
           if p_nachheizung_normal == 0 and p_heizung_direkt_normal == 0:
               st.info("Keine Heizung erforderlich")
       
       # WRG-Einsparung anzeigen
       if wrg_wirkungsgrad > 0:
           wrg_einsparung = luftmassenstrom * prozess_normal['leistungen']['wrg_rueckgewinn'] / 1000
           st.success(f"🔄 WRG-Wärmerückgewinnung: **{wrg_einsparung:.1f} kW** (eingesparte Energie)")
   
   with tab3:
       st.subheader("📈 Kostenverteilung")
       
       col1, col2 = st.columns(2)
       
       with col1:
           # Kostenverteilung Kreisdiagramm
           kosten_data = []
           labels_data = []
           colors = []
           
           if kosten_ventilator > 0:
               kosten_data.append(kosten_ventilator)
               labels_data.append("Ventilator")
               colors.append("#FF6B6B")
           
           if kosten_heizen > 0:
               kosten_data.append(kosten_heizen)
               labels_data.append("Heizung")
               colors.append("#4ECDC4")
           
           if kosten_kuehlen > 0:
               kosten_data.append(kosten_kuehlen)
               labels_data.append("Kühlung")
               colors.append("#45B7D1")
           
           if kosten_data:
               fig_pie = go.Figure(data=[go.Pie(
                   labels=labels_data,
                   values=kosten_data,
                   hole=0.4,
                   marker=dict(colors=colors),
                   textinfo='label+percent+value',
                   texttemplate='%{label}<br>%{percent}<br>%{value:.0f}€'
               )])
               fig_pie.update_layout(
                   title="Jahreskosten-Verteilung",
                   height=400,
                   showlegend=False
               )
               st.plotly_chart(fig_pie, use_container_width=True)
       
       with col2:
           # Leistungsverteilung Balkendiagramm
           leistungen_data = []
           leistungen_labels = []
           leistungen_colors = []
           
           if p_ventilator > 0:
               leistungen_data.append(p_ventilator * teillast_faktor)
               leistungen_labels.append("Ventilator")
               leistungen_colors.append("#FF6B6B")
           
           if p_heizen_normal > 0:
               leistungen_data.append(p_heizen_normal)
               leistungen_labels.append("Heizung")
               leistungen_colors.append("#4ECDC4")
           
           if p_kuehlen_normal > 0:
               leistungen_data.append(p_kuehlen_normal)
               leistungen_labels.append("Kühlung")
               leistungen_colors.append("#45B7D1")
           
           if leistungen_data:
               fig_bar = go.Figure(data=[go.Bar(
                   x=leistungen_labels,
                   y=leistungen_data,
                   marker_color=leistungen_colors,
                   text=[f"{val:.1f} kW" for val in leistungen_data],
                   textposition='auto'
               )])
               fig_bar.update_layout(
                   title="Installierte Leistungen",
                   height=400,
                   yaxis_title="Leistung [kW]",
                   showlegend=False
               )
               st.plotly_chart(fig_bar, use_container_width=True)
       
       # Zusätzliche Kennzahlen
       st.markdown("---")
       st.subheader("📋 Kennzahlen")
       
       col1, col2, col3, col4 = st.columns(4)
       
       with col1:
           spez_kosten_m3h = gesamtkosten / (volumenstrom * jahresstunden['gesamt'] / 1000) if jahresstunden['gesamt'] > 0 else 0
           st.metric("Spez. Kosten", f"{spez_kosten_m3h:.3f} €/(m³·h)")
       
       with col2:
           spez_leistung = p_gesamt / volumenstrom * 1000
           st.metric("Spez. Leistung", f"{spez_leistung:.2f} W/m³/h")
       
       with col3:
           jahresenergie = ((p_heizen_normal * jahresstunden['normal'] + p_heizen_abgesenkt * jahresstunden['abgesenkt']) +
                          (p_kuehlen_normal * jahresstunden['normal'] + p_kuehlen_abgesenkt * jahresstunden['abgesenkt']) +
                          (p_ventilator * teillast_faktor * jahresstunden['gesamt'])) / 1000  # MWh
           st.metric("Jahresenergie", f"{jahresenergie:.1f} MWh/a")
       
       with col4:
           co2_faktor_strom = 0.4  # kg CO2/kWh (deutscher Strommix)
           co2_faktor_waerme = 0.2  # kg CO2/kWh (Fernwärme)
           co2_emissionen = (kosten_ventilator/preis_strom * co2_faktor_strom + 
                            kosten_heizen/preis_waerme * co2_faktor_waerme) / 1000  # t CO2
           st.metric("CO₂-Emissionen", f"{co2_emissionen:.1f} t/a")

# Footer mit PDF-Export
st.markdown("---")
col1, col2, col3 = st.columns([2, 1, 2])

with col2:
   if st.button("📄 Professional PDF-Report", type="primary", use_container_width=True):
       buffer = BytesIO()
       p = canvas.Canvas(buffer, pagesize=A4)
       width, height = A4
       
       # Header
       p.setFont("Helvetica-Bold", 18)
       p.drawString(50, height-50, "RLT-Anlagen Kostenanalyse Professional")
       p.setFont("Helvetica", 10)
       p.drawString(50, height-75, f"Erstellt am: {pd.Timestamp.now().strftime('%d.%m.%Y %H:%M Uhr')}")
       
       y = height - 120
       
       # Anlagendaten
       p.setFont("Helvetica-Bold", 14)
       p.drawString(50, y, "Anlagendaten")
       y -= 25
       
       p.setFont("Helvetica", 11)
       anlagendaten = [
           f"Volumenstrom: {volumenstrom:,.0f} m³/h",
           f"Spezifische Ventilatorleistung: {sfp:.1f} W/(m³/h)",
           f"Teillastfaktor: {teillast_faktor*100:.0f}%",
           f"WRG-Wirkungsgrad: {wrg_wirkungsgrad*100:.0f}%" if wrg_vorhanden else "Keine Wärmerückgewinnung",
           ""
       ]
       
       for item in anlagendaten:
           p.drawString(70, y, item)
           y -= 18
       
       # Klimabedingungen
       p.setFont("Helvetica-Bold", 14)
       p.drawString(50, y, "Klimabedingungen")
       y -= 25
       
       p.setFont("Helvetica", 11)
       klimadaten = [
           f"Außenluft: {temp_aussen:.1f}°C, {feuchte_aussen:.1f}% rF",
           f"Zuluft-Soll: {temp_zuluft:.1f}°C" + (f", {feuchte_zuluft_soll:.1f}% rF" if entfeuchten else ""),
           f"Entfeuchtung: {'Aktiv' if entfeuchten else 'Inaktiv'}",
           ""
       ]
       
       for item in klimadaten:
           p.drawString(70, y, item)
           y -= 18
       
       # Betriebszeiten
       p.setFont("Helvetica-Bold", 14)
       p.drawString(50, y, "Betriebszeiten")
       y -= 25
       
       p.setFont("Helvetica", 11)
       betriebsdaten = [
           f"Normalbetrieb: {jahresstunden['normal']:,.0f} h/Jahr",
           f"Absenkbetrieb: {jahresstunden['abgesenkt']:,.0f} h/Jahr" if absenk_aktiv else "Kein Absenkbetrieb",
           f"Wartung: {wartung_tage} Tage/Jahr, Betriebsferien: {ferien_tage} Tage/Jahr",
           f"Verfügbare Betriebstage: {jahresstunden['verfugbare_tage']} Tage/Jahr",
           ""
       ]
       
       for item in betriebsdaten:
           p.drawString(70, y, item)
           y -= 18
       
       # Leistungen
       p.setFont("Helvetica-Bold", 14)
       p.drawString(50, y, "Installierte Leistungen")
       y -= 25
       
       p.setFont("Helvetica", 11)
       leistungsdaten = [
           f"Ventilator: {p_ventilator:.1f} kW",
           f"Heizung: {p_heizen_normal:.1f} kW" + (f" (davon Nachheizung: {p_nachheizung_normal:.1f} kW)" if p_nachheizung_normal > 0 else ""),
           f"Kühlung: {p_kuehlen_normal:.1f} kW" + (f" (davon Entfeuchtung: {p_kuehlung_entf_normal:.1f} kW)" if p_kuehlung_entf_normal > 0 else ""),
           f"Gesamtleistung: {p_gesamt:.1f} kW",
           ""
       ]
       
       for item in leistungsdaten:
           p.drawString(70, y, item)
           y -= 18
       
       # Kosten
       p.setFont("Helvetica-Bold", 14)
       p.drawString(50, y, "Jahreskosten")
       y -= 25
       
       p.setFont("Helvetica", 11)
       kostendaten = [
           f"Ventilator (Strom): {kosten_ventilator:,.0f} €/Jahr",
           f"Heizung (Fernwärme): {kosten_heizen:,.0f} €/Jahr",
           f"Kühlung (Kälte): {kosten_kuehlen:,.0f} €/Jahr",
           "",
           f"GESAMTKOSTEN: {gesamtkosten:,.0f} €/Jahr ({gesamtkosten/12:,.0f} €/Monat)"
       ]
       
       for item in kostendaten:
           if "GESAMTKOSTEN" in item:
               p.setFont("Helvetica-Bold", 12)
           p.drawString(70, y, item)
           p.setFont("Helvetica", 11)
           y -= 18
       
       # Kennzahlen
       y -= 10
       p.setFont("Helvetica-Bold", 14)
       p.drawString(50, y, "Kennzahlen")
       y -= 25
       
       p.setFont("Helvetica", 11)
       kennzahlen = [
           f"Spezifische Kosten: {spez_kosten_m3h:.3f} €/(m³·h)",
           f"Spezifische Leistung: {spez_leistung:.2f} W/m³/h",
           f"Jahresenergieverbrauch: {jahresenergie:.1f} MWh/Jahr",
           f"CO₂-Emissionen: {co2_emissionen:.1f} t/Jahr"
       ]
       
       for item in kennzahlen:
           p.drawString(70, y, item)
           y -= 18
       
       # Fußzeile
       p.setFont("Helvetica-Italic", 8)
       p.drawString(50, 50, "RLT-Anlagen Kostenanalyse Professional v3.0 - Erstellt mit korrekter Thermodynamik")
       p.drawString(50, 35, f"Berechnung basiert auf: Magnus-Formel, h,x-Diagramm, DIN EN 13779")
       
       p.save()
       buffer.seek(0)
       
       st.download_button(
           label="📥 PDF Report herunterladen",
           data=buffer,
           file_name=f"RLT_Professional_Report_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.pdf",
           mime="application/pdf",
           use_container_width=True
       )

st.markdown("---")
st.markdown("*RLT-Anlagen Kostenanalyse Professional v3.0 | Entwickelt mit korrekter Thermodynamik und professioneller Benutzerführung*")
