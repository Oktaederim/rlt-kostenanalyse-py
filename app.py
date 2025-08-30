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
    return taupunkt - sicherheit  # 1K Sicherheit unter Taupunkt

def berechne_luftzustand(temp, rel_feuchte=None, abs_feuchte=None):
    """Vollständiger Luftzustand"""
    if abs_feuchte is None:
        abs_feuchte = abs_feuchte_aus_rel_feuchte(temp, rel_feuchte)
    else:
        rel_feuchte = rel_feuchte_aus_abs_feuchte(temp, abs_feuchte)
    
    return {
        'temperatur': round(temp, 1),
        'rel_feuchte': round(rel_feuchte, 1),
        'abs_feuchte': round(abs_feuchte * 1000, 2),  # g/kg für bessere Lesbarkeit
        'enthalpie': round(enthalpie_feuchte_luft(temp, abs_feuchte) / 1000, 1),  # kJ/kg
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
    zustand_aussen['punkt'] = '1. Außenluft'
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
        zustand_wrg['punkt'] = '2. Nach WRG'
        prozess['schritte'].append(zustand_wrg)
        
        # WRG-Leistung (eingesparte Energie)
        prozess['leistungen']['wrg_rueckgewinn'] = aktueller_zustand['enthalpie'] - enthalpie_feuchte_luft(temp_nach_wrg, abs_feuchte_aussen)
        
        aktueller_zustand['temp'] = temp_nach_wrg
        aktueller_zustand['enthalpie'] = enthalpie_feuchte_luft(temp_nach_wrg, abs_feuchte_aussen)
    
    # Schritt 3: Entfeuchtung (wenn erforderlich)
    ziel_abs_feuchte = abs_feuchte_aus_rel_feuchte(temp_zuluft, feuchte_zuluft_soll)
    
    if entfeuchten and aktueller_zustand['abs_feuchte'] > ziel_abs_feuchte:
        # Erforderliche Kühltemperatur berechnen
        temp_kuehlung = kuhltemperatur_fur_entfeuchtung(ziel_abs_feuchte)
        
        # Zustand nach Kühlung/Entfeuchtung
        zustand_gekuehlt = berechne_luftzustand(temp_kuehlung, abs_feuchte=ziel_abs_feuchte)
        zustand_gekuehlt['punkt'] = f'3. Gekühlt/Entfeuchtet auf {temp_kuehlung}°C'
        prozess['schritte'].append(zustand_gekuehlt)
        
        # Kühlleistung für Entfeuchtung
        enthalpie_gekuehlt = enthalpie_feuchte_luft(temp_kuehlung, ziel_abs_feuchte)
        prozess['leistungen']['kuehlung_entf'] = aktueller_zustand['enthalpie'] - enthalpie_gekuehlt
        
        aktueller_zustand['temp'] = temp_kuehlung
        aktueller_zustand['abs_feuchte'] = ziel_abs_feuchte
        aktueller_zustand['enthalpie'] = enthalpie_gekuehlt
        
        # Schritt 4: Nachheizung auf Solltemperatur
        if temp_kuehlung < temp_zuluft:
            zustand_nachgeheizt = berechne_luftzustand(temp_zuluft, abs_feuchte=ziel_abs_feuchte)
            zustand_nachgeheizt['punkt'] = '4. Nachheizung'
            prozess['schritte'].append(zustand_nachgeheizt)
            
            # Nachheizleistung
            enthalpie_final = enthalpie_feuchte_luft(temp_zuluft, ziel_abs_feuchte)
            prozess['leistungen']['nachheizung'] = enthalpie_final - aktueller_zustand['enthalpie']
    
    else:
        # Keine Entfeuchtung - direkte Temperierung
        if aktueller_zustand['temp'] < temp_zuluft:
            # Direktes Heizen
            zustand_final = berechne_luftzustand(temp_zuluft, abs_feuchte=aktueller_zustand['abs_feuchte'])
            zustand_final['punkt'] = '3. Erwärmung'
            prozess['schritte'].append(zustand_final)
            
            enthalpie_final = enthalpie_feuchte_luft(temp_zuluft, aktueller_zustand['abs_feuchte'])
            prozess['leistungen']['heizung_direkt'] = enthalpie_final - aktueller_zustand['enthalpie']
            
        elif aktueller_zustand['temp'] > temp_zuluft:
            # Direktes Kühlen
            zustand_final = berechne_luftzustand(temp_zuluft, abs_feuchte=aktueller_zustand['abs_feuchte'])
            zustand_final['punkt'] = '3. Kühlung'
            prozess['schritte'].append(zustand_final)
            
            enthalpie_final = enthalpie_feuchte_luft(temp_zuluft, aktueller_zustand['abs_feuchte'])
            prozess['leistungen']['kuehlung_direkt'] = aktueller_zustand['enthalpie'] - enthalpie_final
    
    return prozess

# Haupttitel
st.title("🏭 RLT-Anlagen Kostenanalyse Professional")
st.markdown("*Korrekte thermodynamische Berechnung für RLT-Anlagen*")
st.markdown("---")

# Sidebar
with st.sidebar:
    st.header("📋 Anlagendaten")
    
    volumenstrom = st.number_input("Volumenstrom [m³/h]", value=10000, min_value=100, step=500)
    betriebsstunden_tag = st.slider("Betriebsstunden/Tag", 1, 24, 12)
    betriebstage_jahr = st.number_input("Betriebstage/Jahr", value=250, min_value=50, max_value=365)
    teillast_faktor = st.slider("Ø Teillast [%]", 30, 100, 80) / 100
    
    st.markdown("---")
    st.subheader("🌡️ Klimabedingungen")
    
    temp_aussen = st.slider("Außentemperatur [°C]", -20, 40, 5)
    feuchte_aussen = st.slider("Außenluft rel. Feuchte [%]", 20, 95, 60)
    temp_zuluft = st.slider("Zuluft-Solltemperatur [°C]", 16, 26, 20)
    
    st.markdown("---")
    st.subheader("⚙️ Betriebsmodi")
    
    entfeuchten = st.checkbox("🌊 Entfeuchtung aktiv", False)
    
    if entfeuchten:
        feuchte_zuluft_soll = st.slider("Zuluft-Sollfeuchte [%]", 30, 70, 45)
        st.info(f"💡 Ziel: {temp_zuluft}°C bei {feuchte_zuluft_soll}% rF")
        
        # Zeige erforderliche Kühltemperatur
        ziel_abs_feuchte = abs_feuchte_aus_rel_feuchte(temp_zuluft, feuchte_zuluft_soll)
        temp_kuehl_erf = kuhltemperatur_fur_entfeuchtung(ziel_abs_feuchte)
        st.warning(f"⚠️ Erforderliche Kühltemperatur: {temp_kuehl_erf:.1f}°C")
    else:
        feuchte_zuluft_soll = feuchte_aussen  # Keine Entfeuchtung
        st.info("💡 Nur Temperaturregelung")
    
    st.markdown("---")
    st.subheader("🔄 Wärmerückgewinnung")
    wrg_vorhanden = st.checkbox("WRG vorhanden", True)
    if wrg_vorhanden:
        wrg_wirkungsgrad = st.slider("WRG-Wirkungsgrad [%]", 50, 90, 70) / 100
    else:
        wrg_wirkungsgrad = 0

# Berechnungen
prozess = berechne_rlt_prozess(temp_aussen, feuchte_aussen, temp_zuluft, 
                              feuchte_zuluft_soll, wrg_wirkungsgrad, entfeuchten)

# Hauptbereich
tab1, tab2, tab3, tab4 = st.tabs(["📊 Berechnung", "🔄 Prozess", "📈 Diagramme", "📄 Report"])

with tab1:
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.subheader("⚡ Technische Parameter")
        sfp = st.number_input("SFP [W/(m³/h)]", value=2.5, min_value=0.5, max_value=6.0, step=0.1)
        
        st.subheader("💰 Energiepreise")
        preis_strom = st.number_input("Strom [€/kWh]", value=0.25, format="%.3f", step=0.01)
        preis_waerme = st.number_input("Fernwärme [€/kWh]", value=0.08, format="%.3f", step=0.01)
        preis_kaelte = st.number_input("Kälte [€/kWh]", value=0.15, format="%.3f", step=0.01)
    
    with col2:
        st.subheader("📊 Berechnungsergebnisse")
        
        # Ventilatorleistung
        p_ventilator = volumenstrom * sfp * teillast_faktor / 1000
        
        # Thermische Leistungen aus Prozess [kW]
        luftmassenstrom = volumenstrom * 1.2 / 3600  # kg/s
        
        p_kuehlung_entf = luftmassenstrom * prozess['leistungen']['kuehlung_entf'] * teillast_faktor / 1000
        p_nachheizung = luftmassenstrom * prozess['leistungen']['nachheizung'] * teillast_faktor / 1000
        p_heizung_direkt = luftmassenstrom * prozess['leistungen']['heizung_direkt'] * teillast_faktor / 1000
        p_kuehlung_direkt = luftmassenstrom * prozess['leistungen']['kuehlung_direkt'] * teillast_faktor / 1000
        
        # Gesamte Heiz- und Kühlleistung
        p_heizen_gesamt = p_nachheizung + p_heizung_direkt
        p_kuehlen_gesamt = p_kuehlung_entf + p_kuehlung_direkt
        
        # Jahreskosten
        jahresstunden = betriebsstunden_tag * betriebstage_jahr
        kosten_ventilator = p_ventilator * jahresstunden * preis_strom
        kosten_heizen = p_heizen_gesamt * jahresstunden * preis_waerme
        kosten_kuehlen = p_kuehlen_gesamt * jahresstunden * preis_kaelte
        
        # Ergebnisse anzeigen
        col_a, col_b, col_c = st.columns(3)
        
        with col_a:
            st.metric("🌀 Ventilator", f"{p_ventilator:.1f} kW")
            st.metric("💰 Strom/Jahr", f"{kosten_ventilator:.0f} €")
        
        with col_b:
            st.metric("🔥 Heizung", f"{p_heizen_gesamt:.1f} kW")
            if p_nachheizung > 0:
                st.caption(f"davon Nachheizung: {p_nachheizung:.1f} kW")
            st.metric("💰 Wärme/Jahr", f"{kosten_heizen:.0f} €")
        
        with col_c:
            st.metric("❄️ Kühlung", f"{p_kuehlen_gesamt:.1f} kW")
            if p_kuehlung_entf > 0:
                st.caption(f"davon Entfeuchtung: {p_kuehlung_entf:.1f} kW")
            st.metric("💰 Kälte/Jahr", f"{kosten_kuehlen:.0f} €")
        
        # Gesamtkosten
        gesamtkosten = kosten_ventilator + kosten_heizen + kosten_kuehlen
        st.markdown("### 💯 Gesamtkosten")
        st.metric("", f"{gesamtkosten:.0f} €/Jahr")

with tab2:
    st.subheader("🔄 RLT-Prozess (h,x-Diagramm Werte)")
    
    for i, schritt in enumerate(prozess['schritte']):
        with st.container():
            col1, col2, col3, col4, col5 = st.columns(5)
            
            with col1:
                st.write(f"**{schritt['punkt']}**")
            with col2:
                st.write(f"🌡️ {schritt['temperatur']}°C")
            with col3:
                st.write(f"💧 {schritt['rel_feuchte']}% rF")
            with col4:
                st.write(f"💨 {schritt['abs_feuchte']} g/kg")
            with col5:
                st.write(f"⚡ {schritt['enthalpie']} kJ/kg")
            
            if i < len(prozess['schritte']) - 1:
                st.write("⬇️")
    
    # Leistungen anzeigen
    st.markdown("### 🔧 Prozessleistungen:")
    
    if prozess['leistungen']['kuehlung_entf'] > 0:
        p_kuehl_entf = luftmassenstrom * prozess['leistungen']['kuehlung_entf'] / 1000
        st.info(f"❄️ Kühlung für Entfeuchtung: **{p_kuehl_entf:.1f} kW**")
    
    if prozess['leistungen']['nachheizung'] > 0:
        p_nachhz = luftmassenstrom * prozess['leistungen']['nachheizung'] / 1000
        st.success(f"🔥 Nachheizung: **{p_nachhz:.1f} kW**")
    
    if prozess['leistungen']['heizung_direkt'] > 0:
        p_hz_dir = luftmassenstrom * prozess['leistungen']['heizung_direkt'] / 1000
        st.success(f"🔥 Direkte Heizung: **{p_hz_dir:.1f} kW**")
    
    if prozess['leistungen']['kuehlung_direkt'] > 0:
        p_kl_dir = luftmassenstrom * prozess['leistungen']['kuehlung_direkt'] / 1000
        st.info(f"❄️ Direkte Kühlung: **{p_kl_dir:.1f} kW**")

with tab3:
    st.subheader("💰 Kostenverteilung")
    
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
            hole=0.3
        )])
        fig.update_traces(textposition='inside', textinfo='percent+label')
        fig.update_layout(height=500, showlegend=True)
        st.plotly_chart(fig, use_container_width=True)

with tab4:
    st.subheader("📄 Professional Report")
    
    if st.button("📄 PDF-Report generieren", type="primary"):
        buffer = BytesIO()
        p = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        
        p.setFont("Helvetica-Bold", 16)
        p.drawString(50, height-50, "RLT-Kostenanalyse Professional")
        
        y = height - 100
        p.setFont("Helvetica", 10)
        
        daten = [
            f"Volumenstrom: {volumenstrom:,} m³/h",
            f"Außenluft: {temp_aussen}°C, {feuchte_aussen}% rF",
            f"Zuluft: {temp_zuluft}°C" + (f", {feuchte_zuluft_soll}% rF" if entfeuchten else ""),
            "",
            "Leistungen:",
            f"  Ventilator: {p_ventilator:.1f} kW",
            f"  Heizung gesamt: {p_heizen_gesamt:.1f} kW",
            f"  Kühlung gesamt: {p_kuehlen_gesamt:.1f} kW",
            "",
            f"Jahreskosten: {gesamtkosten:.0f} €/Jahr"
        ]
        
        for item in daten:
            p.drawString(70, y, item)
            y -= 15
        
        p.save()
        buffer.seek(0)
        
        st.download_button(
            label="📥 PDF herunterladen",
            data=buffer,
            file_name=f"RLT_Professional_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.pdf",
            mime="application/pdf"
        )

st.markdown("---")
st.markdown("*RLT Professional v3.0 - Korrekte Thermodynamik*")
