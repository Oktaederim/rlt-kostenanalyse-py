import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

st.set_page_config(
    page_title="RLT-Anlagen Kostenanalyse Professional",
    page_icon="🏭",
    layout="wide"
)

def saettigungsdampfdruck(temp):
    return 611.2 * np.exp((17.67 * temp) / (temp + 243.5))

def abs_feuchte_aus_rel_feuchte(temp, rel_feuchte):
    es = saettigungsdampfdruck(temp)
    e = rel_feuchte / 100 * es
    return 0.622 * e / (101325 - e)

def rel_feuchte_aus_abs_feuchte(temp, abs_feuchte):
    es = saettigungsdampfdruck(temp)
    e = abs_feuchte * 101325 / (0.622 + abs_feuchte)
    return min(100, e / es * 100)

def enthalpie_feuchte_luft(temp, abs_feuchte):
    return 1005 * temp + abs_feuchte * (2501000 + 1870 * temp)

def taupunkt_aus_abs_feuchte(abs_feuchte):
    if abs_feuchte <= 0:
        return -50
    e = abs_feuchte * 101325 / (0.622 + abs_feuchte)
    return 243.5 * np.log(e/611.2) / (17.67 - np.log(e/611.2))

def kuhltemperatur_fur_entfeuchtung(ziel_abs_feuchte, sicherheit=1):
    taupunkt = taupunkt_aus_abs_feuchte(ziel_abs_feuchte)
    return taupunkt - sicherheit

def berechne_luftzustand(temp, rel_feuchte=None, abs_feuchte=None):
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
    prozess = {
        'schritte': [],
        'leistungen': {
            'kuehlung_entf': 0,
            'nachheizung': 0,
            'heizung_direkt': 0,
            'kuehlung_direkt': 0
        }
    }
    
    # Außenluft
    abs_feuchte_aussen = abs_feuchte_aus_rel_feuchte(temp_aussen, feuchte_aussen)
    zustand_aussen = berechne_luftzustand(temp_aussen, feuchte_aussen)
    zustand_aussen['punkt'] = 'Außenluft'
    prozess['schritte'].append(zustand_aussen)
    
    aktuelle_temp = temp_aussen
    aktuelle_abs_feuchte = abs_feuchte_aussen
    
    # WRG
    if wrg_grad > 0:
        temp_nach_wrg = temp_aussen + (temp_zuluft - temp_aussen) * wrg_grad
        zustand_wrg = berechne_luftzustand(temp_nach_wrg, abs_feuchte=abs_feuchte_aussen)
        zustand_wrg['punkt'] = 'Nach WRG'
        prozess['schritte'].append(zustand_wrg)
        aktuelle_temp = temp_nach_wrg
    
    # Entfeuchtung
    ziel_abs_feuchte = abs_feuchte_aus_rel_feuchte(temp_zuluft, feuchte_zuluft_soll)
    
    if entfeuchten and aktuelle_abs_feuchte > ziel_abs_feuchte:
        temp_kuehlung = kuhltemperatur_fur_entfeuchtung(ziel_abs_feuchte)
        zustand_gekuehlt = berechne_luftzustand(temp_kuehlung, abs_feuchte=ziel_abs_feuchte)
        zustand_gekuehlt['punkt'] = f'Gekühlt auf {temp_kuehlung:.1f}°C'
        prozess['schritte'].append(zustand_gekuehlt)
        
        # Kühlenergie für Entfeuchtung
        enthalpie_vorher = enthalpie_feuchte_luft(aktuelle_temp, aktuelle_abs_feuchte)
        enthalpie_nachher = enthalpie_feuchte_luft(temp_kuehlung, ziel_abs_feuchte)
        prozess['leistungen']['kuehlung_entf'] = enthalpie_vorher - enthalpie_nachher
        
        aktuelle_temp = temp_kuehlung
        aktuelle_abs_feuchte = ziel_abs_feuchte
        
        # Nachheizung
        if temp_kuehlung < temp_zuluft:
            zustand_nachgeheizt = berechne_luftzustand(temp_zuluft, abs_feuchte=ziel_abs_feuchte)
            zustand_nachgeheizt['punkt'] = 'Nachheizung'
            prozess['schritte'].append(zustand_nachgeheizt)
            
            enthalpie_vorher = enthalpie_feuchte_luft(temp_kuehlung, ziel_abs_feuchte)
            enthalpie_nachher = enthalpie_feuchte_luft(temp_zuluft, ziel_abs_feuchte)
            prozess['leistungen']['nachheizung'] = enthalpie_nachher - enthalpie_vorher
    
    else:
        # Keine Entfeuchtung - direkte Temperierung
        if aktuelle_temp < temp_zuluft:
            zustand_final = berechne_luftzustand(temp_zuluft, abs_feuchte=aktuelle_abs_feuchte)
            zustand_final['punkt'] = 'Erwärmung'
            prozess['schritte'].append(zustand_final)
            
            enthalpie_vorher = enthalpie_feuchte_luft(aktuelle_temp, aktuelle_abs_feuchte)
            enthalpie_nachher = enthalpie_feuchte_luft(temp_zuluft, aktuelle_abs_feuchte)
            prozess['leistungen']['heizung_direkt'] = enthalpie_nachher - enthalpie_vorher
            
        elif aktuelle_temp > temp_zuluft:
            zustand_final = berechne_luftzustand(temp_zuluft, abs_feuchte=aktuelle_abs_feuchte)
            zustand_final['punkt'] = 'Kühlung'
            prozess['schritte'].append(zustand_final)
            
            enthalpie_vorher = enthalpie_feuchte_luft(aktuelle_temp, aktuelle_abs_feuchte)
            enthalpie_nachher = enthalpie_feuchte_luft(temp_zuluft, aktuelle_abs_feuchte)
            prozess['leistungen']['kuehlung_direkt'] = enthalpie_vorher - enthalpie_nachher
    
    return prozess

# Haupttitel
st.title("🏭 RLT-Anlagen Kostenanalyse Professional")
st.markdown("---")

# Layout
col_left, col_right = st.columns([1, 2])

with col_left:
    st.header("⚙️ Eingaben")
    
    # Betriebsmodi
    with st.expander("🔧 Betriebsmodi", expanded=True):
        entfeuchten = st.checkbox("🌊 Entfeuchtung aktiv", False)
        wrg_vorhanden = st.checkbox("🔄 WRG vorhanden", True)
        if wrg_vorhanden:
            wrg_wirkungsgrad = st.slider("WRG-Wirkungsgrad [%]", 50, 95, 70) / 100
        else:
            wrg_wirkungsgrad = 0
    
    # Anlagenparameter
    with st.expander("📋 Anlagenparameter", expanded=True):
        volumenstrom = st.slider("Volumenstrom [m³/h]", 100, 500000, 10000, step=500)
        volumenstrom = st.number_input("Exakter Volumenstrom", value=volumenstrom, min_value=100, max_value=500000)
        
        sfp = st.slider("SFP [W/(m³/h)]", 0.5, 6.0, 2.5, step=0.1)
        sfp = st.number_input("SFP exakt", value=sfp, min_value=0.5, max_value=6.0, step=0.1)
        
        teillast_faktor = st.slider("Teillast [%]", 30, 100, 80) / 100
    
    # Außenluftbedingungen
    with st.expander("🌤️ Außenluftbedingungen", expanded=True):
        temp_aussen = st.slider("Außentemperatur [°C]", -20, 45, 5)
        temp_aussen = st.number_input("Exakte Außentemp. [°C]", value=float(temp_aussen), min_value=-20.0, max_value=45.0, step=0.5)
        
        feuchte_aussen = st.slider("Außenluft rel. Feuchte [%]", 20, 95, 60)
        feuchte_aussen = st.number_input("Exakte Außenfeuchte [%]", value=feuchte_aussen, min_value=20, max_value=95)
    
    # Zuluftbedingungen
    with st.expander("🎯 Zuluftbedingungen", expanded=True):
        temp_zuluft = st.slider("Zuluft-Solltemperatur [°C]", 16, 30, 20)
        temp_zuluft = st.number_input("Exakte Zuluft-Temp. [°C]", value=float(temp_zuluft), min_value=16.0, max_value=30.0, step=0.5)
        
        if entfeuchten:
            feuchte_modus = st.radio("Feuchte-Eingabe:", ["Relative Feuchte [%]", "Absolute Feuchte [g/kg]"])
            
            if feuchte_modus == "Relative Feuchte [%]":
                feuchte_zuluft_soll = st.slider("Zuluft rel. Feuchte [%]", 30, 70, 45)
                feuchte_zuluft_soll = st.number_input("Exakte rel. Feuchte [%]", value=feuchte_zuluft_soll, min_value=30, max_value=70)
                abs_feuchte_berechnet = abs_feuchte_aus_rel_feuchte(temp_zuluft, feuchte_zuluft_soll) * 1000
                st.info(f"💡 = {abs_feuchte_berechnet:.2f} g/kg abs. Feuchte")
            else:
                abs_feuchte_ziel = st.slider("Zuluft abs. Feuchte [g/kg]", 5.0, 15.0, 8.0, step=0.1)
                abs_feuchte_ziel = st.number_input("Exakte abs. Feuchte [g/kg]", value=abs_feuchte_ziel, min_value=5.0, max_value=15.0, step=0.1)
                feuchte_zuluft_soll = rel_feuchte_aus_abs_feuchte(temp_zuluft, abs_feuchte_ziel/1000)
                st.info(f"💡 = {feuchte_zuluft_soll:.1f}% rel. Feuchte bei {temp_zuluft}°C")
        else:
            feuchte_zuluft_soll = feuchte_aussen
    
    # Betriebszeiten
    with st.expander("⏰ Betriebszeiten", expanded=True):
        normal_tage = st.slider("Normalbetrieb Tage/Woche", 1, 7, 5)
        normal_stunden = st.slider("Normalbetrieb Stunden/Tag", 1, 24, 12)
        
        absenk_aktiv = st.checkbox("Absenkbetrieb", True)
        if absenk_aktiv:
            absenk_tage = st.slider("Absenk Tage/Woche", 0, 7, 2)
            absenk_stunden = st.slider("Absenk Stunden/Tag", 1, 24, 8)
            absenk_reduktion = st.slider("Leistungsreduktion [%]", 10, 70, 50) / 100
        else:
            absenk_tage = absenk_stunden = 0
            absenk_reduktion = 0
        
        wartung_tage = st.slider("Wartung [Tage/Jahr]", 0, 30, 5)
        ferien_tage = st.slider("Betriebsferien [Tage/Jahr]", 0, 60, 14)
    
    # Energiepreise
    with st.expander("💰 Energiepreise", expanded=True):
        preis_strom = st.number_input("Strom [€/kWh]", value=0.25, format="%.3f")
        preis_waerme = st.number_input("Fernwärme [€/kWh]", value=0.08, format="%.3f")
        preis_kaelte = st.number_input("Kälte [€/kWh]", value=0.15, format="%.3f")

with col_right:
    st.header("📊 Ergebnisse")
    
    # Berechnungen
    prozess = berechne_rlt_prozess(temp_aussen, feuchte_aussen, temp_zuluft, feuchte_zuluft_soll, wrg_wirkungsgrad, entfeuchten)
    
    # Betriebsstunden
    verfugbare_tage = 365 - wartung_tage - ferien_tage
    normal_stunden_jahr = normal_tage * normal_stunden * 52
    absenk_stunden_jahr = absenk_tage * absenk_stunden * 52 if absenk_aktiv else 0
    gesamt_stunden_jahr = normal_stunden_jahr + absenk_stunden_jahr
    
    # Leistungen
    luftmassenstrom = volumenstrom * 1.2 / 3600
    p_ventilator = volumenstrom * sfp / 1000
    
    p_kuehlung_entf = luftmassenstrom * prozess['leistungen']['kuehlung_entf'] / 1000
    p_nachheizung = luftmassenstrom * prozess['leistungen']['nachheizung'] / 1000
    p_heizung_direkt = luftmassenstrom * prozess['leistungen']['heizung_direkt'] / 1000
    p_kuehlung_direkt = luftmassenstrom * prozess['leistungen']['kuehlung_direkt'] / 1000
    
    p_heizen_gesamt = p_nachheizung + p_heizung_direkt
    p_kuehlen_gesamt = p_kuehlung_entf + p_kuehlung_direkt
    
    # Kosten
    kosten_ventilator = p_ventilator * teillast_faktor * gesamt_stunden_jahr * preis_strom
    kosten_heizen = p_heizen_gesamt * normal_stunden_jahr * preis_waerme
    if absenk_aktiv:
        kosten_heizen += p_heizen_gesamt * absenk_reduktion * absenk_stunden_jahr * preis_waerme
    
    kosten_kuehlen = p_kuehlen_gesamt * normal_stunden_jahr * preis_kaelte
    if absenk_aktiv:
        kosten_kuehlen += p_kuehlen_gesamt * absenk_reduktion * absenk_stunden_jahr * preis_kaelte
    
    gesamtkosten = kosten_ventilator + kosten_heizen + kosten_kuehlen
    
    # Ergebnisse anzeigen
    tab1, tab2, tab3 = st.tabs(["⚡ Leistungen", "🔄 Prozess", "📈 Diagramme"])
    
    with tab1:
        st.subheader("⚡ Installierte Leistungen")
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("🌀 Ventilator", f"{p_ventilator:.1f} kW")
        
        with col2:
            st.metric("🔥 Heizung", f"{p_heizen_gesamt:.1f} kW")
            if p_nachheizung > 0:
                st.caption(f"Nachheizung: {p_nachheizung:.1f} kW")
        
        with col3:
            st.metric("❄️ Kühlung", f"{p_kuehlen_gesamt:.1f} kW")
            if p_kuehlung_entf > 0:
                st.caption(f"Entfeuchtung: {p_kuehlung_entf:.1f} kW")
        
        with col4:
            p_gesamt = p_ventilator + p_heizen_gesamt + p_kuehlen_gesamt
            st.metric("⚡ Gesamt", f"{p_gesamt:.1f} kW")
        
        st.markdown("---")
        st.subheader("💰 Jahreskosten")
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("🌀 Ventilator", f"{kosten_ventilator:.0f} €/a")
        
        with col2:
            st.metric("🔥 Heizung", f"{kosten_heizen:.0f} €/a")
        
        with col3:
            st.metric("❄️ Kühlung", f"{kosten_kuehlen:.0f} €/a")
        
        with col4:
            st.metric("💯 Gesamt", f"{gesamtkosten:.0f} €/a")
        
        # Betriebszeiten
        st.markdown("---")
        st.subheader("⏰ Betriebszeiten")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Normal", f"{normal_stunden_jahr:,.0f} h/a")
        with col2:
            st.metric("Absenk", f"{absenk_stunden_jahr:,.0f} h/a")
        with col3:
            st.metric("Verfügbar", f"{verfugbare_tage} Tage/a")
    
    with tab2:
        st.subheader("🔄 Luftbehandlungsprozess")
        
        # Prozess-Tabelle
        df_prozess = pd.DataFrame(prozess['schritte'])
        df_prozess.columns = ['Prozessschritt', 'Temperatur [°C]', 'rel. Feuchte [%]', 'abs. Feuchte [g/kg]', 'Enthalpie [kJ/kg]', 'Taupunkt [°C]']
        st.dataframe(df_prozess, use_container_width=True, hide_index=True)
        
        st.markdown("---")
        st.subheader("🔧 Prozessleistungen")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**Kühlleistungen:**")
            if p_kuehlung_entf > 0:
                st.success(f"❄️ Entfeuchtung: {p_kuehlung_entf:.1f} kW")
            if p_kuehlung_direkt > 0:
                st.info(f"❄️ Direkte Kühlung: {p_kuehlung_direkt:.1f} kW")
            if p_kuehlung_entf == 0 and p_kuehlung_direkt == 0:
                st.info("Keine Kühlung erforderlich")
        
        with col2:
            st.markdown("**Heizleistungen:**")
            if p_nachheizung > 0:
                st.warning(f"🔥 Nachheizung: {p_nachheizung:.1f} kW")
            if p_heizung_direkt > 0:
                st.warning(f"🔥 Direkte Heizung: {p_heizung_direkt:.1f} kW")
            if p_nachheizung == 0 and p_heizung_direkt == 0:
                st.info("Keine Heizung erforderlich")
    
    with tab3:
        st.subheader("📈 Kostenverteilung")
        
        # Kreisdiagramm
        kosten_data = []
        labels_data = []
        
        if kosten_ventilator > 0:
            kosten_data.append(kosten_ventilator)
            labels_data.append("Ventilator")
        
        if kosten_heizen > 0:
            kosten_data.append(kosten_heizen)
            labels_data.append("Heizung")
        
        if kosten_kuehlen > 0:
            kosten_data.append(kosten_kuehlen)
            labels_data.append("Kühlung")
        
        if kosten_data:
            fig = go.Figure(data=[go.Pie(
                labels=labels_data,
                values=kosten_data,
                hole=0.4
            )])
            fig.update_traces(textposition='inside', textinfo='percent+label')
            fig.update_layout(height=400)
            st.plotly_chart(fig, use_container_width=True)

# PDF Export
st.markdown("---")
if st.button("📄 PDF-Report generieren", type="primary"):
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, height-50, "RLT-Anlagen Kostenanalyse")
    
    y = height - 100
    p.setFont("Helvetica", 10)
    
    daten = [
        f"Volumenstrom: {volumenstrom:,} m³/h",
        f"Außenluft: {temp_aussen}°C, {feuchte_aussen}% rF",
        f"Zuluft: {temp_zuluft}°C, {feuchte_zuluft_soll:.1f}% rF",
        f"Ventilator: {p_ventilator:.1f} kW",
        f"Heizung: {p_heizen_gesamt:.1f} kW",
        f"Kühlung: {p_kuehlen_gesamt:.1f} kW",
        f"Jahreskosten: {gesamtkosten:.0f} €"
    ]
    
    for item in daten:
        p.drawString(70, y, item)
        y -= 20
    
    p.save()
    buffer.seek(0)
    
    st.download_button(
        label="📥 PDF herunterladen",
        data=buffer,
        file_name=f"RLT_Report_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.pdf",
        mime="application/pdf"
    )

st.markdown("---")
st.markdown("*RLT Professional v3.1 - Fehlerfreie Version*")
