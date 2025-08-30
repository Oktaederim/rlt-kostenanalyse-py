import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import plotly.graph_objects as go
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
import psychrolib

# Psychrolib konfigurieren
psychrolib.SetUnitSystem(psychrolib.SI)

# Seiten-Konfiguration
st.set_page_config(
    page_title="RLT-Anlagen Kostenanalyse Professional",
    page_icon="🏭",
    layout="wide"
)

def berechne_luftzustand(temp, rel_feuchte):
    """Berechnet Luftzustand mit psychrometrischen Eigenschaften"""
    try:
        abs_feuchte = psychrolib.GetHumRatioFromRelHum(temp, rel_feuchte/100, 101325)
        enthalpie = psychrolib.GetMoistAirEnthalpy(temp, abs_feuchte)
        taupunkt = psychrolib.GetTDewPointFromHumRatio(temp, abs_feuchte, 101325)
        return {
            'temperatur': temp,
            'rel_feuchte': rel_feuchte,
            'abs_feuchte': abs_feuchte,
            'enthalpie': enthalpie,
            'taupunkt': taupunkt
        }
    except:
        return None

def berechne_rlt_prozess(aussen, soll_temp, soll_feuchte, wrg_grad, betriebsmodi):
    """Berechnet kompletten RLT-Prozess"""
    prozess = {'schritte': []}
    
    # Startpunkt: Außenluft
    zustand = aussen.copy()
    zustand['punkt'] = 'Außenluft'
    prozess['schritte'].append(zustand.copy())
    
    # Schritt 1: Wärmerückgewinnung (wenn vorhanden)
    if wrg_grad > 0:
        # Vereinfachte WRG: Temperaturanhebung
        temp_nach_wrg = zustand['temperatur'] + (soll_temp - zustand['temperatur']) * wrg_grad
        zustand_wrg = berechne_luftzustand(temp_nach_wrg, zustand['rel_feuchte'])
        if zustand_wrg:
            zustand_wrg['punkt'] = 'Nach WRG'
            prozess['schritte'].append(zustand_wrg)
            zustand = zustand_wrg
    
    # Schritt 2: Entfeuchtung (wenn erforderlich)
    if betriebsmodi['entfeuchten'] and zustand['abs_feuchte'] > soll_feuchte:
        # Kühlen bis Taupunkt der Sollfeuchte
        taupunkt_soll = psychrolib.GetTDewPointFromHumRatio(soll_temp, soll_feuchte, 101325)
        
        if zustand['temperatur'] > taupunkt_soll:
            # Kühlen und entfeuchten
            zustand_entf = berechne_luftzustand(taupunkt_soll, 100)  # Sättigung am Taupunkt
            if zustand_entf:
                zustand_entf['punkt'] = 'Nach Entfeuchtung'
                prozess['schritte'].append(zustand_entf)
                zustand = zustand_entf
                
                # Kühlenergie berechnen
                prozess['kuehlung_entf'] = aussen['enthalpie'] - zustand['enthalpie']
    
    # Schritt 3: Heizen/Kühlen auf Solltemperatur
    if zustand['temperatur'] != soll_temp:
        zustand_final = berechne_luftzustand(soll_temp, zustand['rel_feuchte'])
        if zustand_final:
            # Feuchte anpassen falls entfeuchtet wurde
            if 'kuehlung_entf' in prozess:
                zustand_final = berechne_luftzustand(soll_temp, 
                    psychrolib.GetRelHumFromHumRatio(soll_temp, soll_feuchte, 101325) * 100)
            
            zustand_final['punkt'] = 'Zuluft'
            prozess['schritte'].append(zustand_final)
            
            # Heiz-/Kühlenergie berechnen
            if len(prozess['schritte']) >= 2:
                vorheriger_zustand = prozess['schritte'][-2]
                energie_diff = zustand_final['enthalpie'] - vorheriger_zustand['enthalpie']
                
                if energie_diff > 0:
                    prozess['heizung'] = energie_diff
                else:
                    prozess['kuehlung'] = abs(energie_diff)
    
    return prozess

# Haupttitel
st.title("🏭 RLT-Anlagen Kostenanalyse Professional")
st.markdown("*Thermodynamisch korrekte Berechnung mit psychrometrischen Eigenschaften*")
st.markdown("---")

# Sidebar für Eingaben
with st.sidebar:
    st.header("📋 Anlagendaten")
    
    # Grunddaten
    col1, col2 = st.columns(2)
    with col1:
        volumenstrom = st.number_input("Volumenstrom [m³/h]", value=10000, min_value=100, step=500)
        betriebsstunden_tag = st.slider("Betriebsstunden/Tag", 1, 24, 12)
    with col2:
        betriebstage_jahr = st.number_input("Betriebstage/Jahr", value=250, min_value=50, max_value=365)
        teillast_faktor = st.slider("Ø Teillast [%]", 30, 100, 80) / 100
    
    st.markdown("---")
    st.subheader("🌡️ Klimabedingungen")
    
    col1, col2 = st.columns(2)
    with col1:
        temp_aussen = st.slider("Außentemperatur [°C]", -20, 40, 5)
        temp_zuluft = st.slider("Zuluft-Solltemperatur [°C]", 16, 26, 20)
    with col2:
        feuchte_aussen = st.slider("Außenluft rel. Feuchte [%]", 20, 95, 60)
        
    st.markdown("---")
    st.subheader("⚙️ Betriebsmodi")
    
    # Betriebsmodi mit logischer Verknüpfung
    entfeuchten = st.checkbox("🌊 Entfeuchtung aktiv", False)
    
    if entfeuchten:
        feuchte_zuluft_rel = st.slider("Zuluft-Sollfeuchte [%]", 30, 70, 50)
        # Berechne absolute Sollfeuchte
        soll_feuchte_abs = psychrolib.GetHumRatioFromRelHum(temp_zuluft, feuchte_zuluft_rel/100, 101325)
        st.info(f"💡 Entfeuchtung aktiviert: Kühlen → Kondensieren → Nachheizen")
    else:
        soll_feuchte_abs = None
        st.info("💡 Nur Temperaturregelung: Heizen oder Kühlen je nach Bedarf")
    
    # Automatische Betriebsmodi basierend auf Bedingungen
    betriebsmodi = {
        'entfeuchten': entfeuchten,
        'heizen': temp_zuluft > temp_aussen,
        'kuehlen': temp_zuluft < temp_aussen or entfeuchten
    }
    
    st.markdown("---")
    st.subheader("🔄 Wärmerückgewinnung")
    wrg_vorhanden = st.checkbox("WRG vorhanden", True)
    if wrg_vorhanden:
        wrg_wirkungsgrad = st.slider("WRG-Wirkungsgrad [%]", 50, 90, 70) / 100
        wrg_typ = st.selectbox("WRG-Typ", ["Plattenwärmetauscher", "Rotationswärmetauscher", "KVS"])
    else:
        wrg_wirkungsgrad = 0
        wrg_typ = "Keine"

# Hauptbereich
tab1, tab2, tab3, tab4 = st.tabs(["📊 Berechnung", "🔄 Prozess", "📈 Diagramme", "📄 Report"])

# Berechnungen durchführen
aussen_zustand = berechne_luftzustand(temp_aussen, feuchte_aussen)

if aussen_zustand and soll_feuchte_abs is not None:
    prozess = berechne_rlt_prozess(aussen_zustand, temp_zuluft, soll_feuchte_abs, wrg_wirkungsgrad, betriebsmodi)
else:
    # Vereinfachter Prozess ohne Entfeuchtung
    prozess = {'schritte': [aussen_zustand], 'heizung': 0, 'kuehlung': 0}
    if temp_zuluft > temp_aussen:
        # Heizenergie (vereinfacht)
        delta_h = (temp_zuluft - temp_aussen) * 1.005 * 1000  # J/kg
        prozess['heizung'] = delta_h
    elif temp_zuluft < temp_aussen:
        # Kühlenergie (vereinfacht)
        delta_h = (temp_aussen - temp_zuluft) * 1.005 * 1000  # J/kg
        prozess['kuehlung'] = delta_h

with tab1:
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.subheader("⚡ Technische Parameter")
        sfp = st.number_input("Spez. Ventilatorleistung SFP [W/(m³/h)]", 
                             value=2.5, min_value=0.5, max_value=6.0, step=0.1)
        
        st.subheader("💰 Energiepreise")
        preis_strom = st.number_input("Strompreis [€/kWh]", value=0.25, format="%.3f", step=0.01)
        preis_waerme = st.number_input("Fernwärmepreis [€/kWh]", value=0.08, format="%.3f", step=0.01)
        preis_kaelte = st.number_input("Kältepreis [€/kWh]", value=0.15, format="%.3f", step=0.01)
        
        # Zusatzkosten (optional)
        with st.expander("🔧 Zusatzkosten (optional)"):
            wartung_jahr = st.number_input("Wartungskosten [€/Jahr]", value=0, min_value=0)
            filter_kosten = st.number_input("Filterkosten [€/Jahr]", value=0, min_value=0)
    
    with col2:
        st.subheader("📊 Berechnungsergebnisse")
        
        # Ventilatorleistung
        p_ventilator = volumenstrom * sfp * teillast_faktor / 1000  # kW
        
        # Thermische Leistungen
        luftmassenstrom = volumenstrom * 1.2 / 3600  # kg/s
        
        p_heizen = 0
        p_kuehlen = 0
        
        if 'heizung' in prozess and prozess['heizung'] > 0:
            p_heizen = luftmassenstrom * prozess['heizung'] * teillast_faktor / 1000  # kW
        
        if 'kuehlung' in prozess and prozess['kuehlung'] > 0:
            p_kuehlen = luftmassenstrom * prozess['kuehlung'] * teillast_faktor / 1000  # kW
        
        if 'kuehlung_entf' in prozess and prozess['kuehlung_entf'] > 0:
            p_kuehlen += luftmassenstrom * prozess['kuehlung_entf'] * teillast_faktor / 1000  # kW
        
        # Jahreskosten berechnen
        jahresstunden = betriebsstunden_tag * betriebstage_jahr
        
        kosten_ventilator = p_ventilator * jahresstunden * preis_strom
        kosten_heizen = p_heizen * jahresstunden * preis_waerme
        kosten_kuehlen = p_kuehlen * jahresstunden * preis_kaelte
        kosten_zusatz = wartung_jahr + filter_kosten
        
        # Ergebnisse anzeigen
        col_a, col_b, col_c = st.columns(3)
        
        with col_a:
            st.metric("🌀 Ventilatorleistung", f"{p_ventilator:.1f} kW")
            st.metric("💰 Ventilator/Jahr", f"{kosten_ventilator:.0f} €")
        
        with col_b:
            st.metric("🔥 Heizleistung", f"{p_heizen:.1f} kW")
            st.metric("💰 Heizung/Jahr", f"{kosten_heizen:.0f} €")
        
        with col_c:
            st.metric("❄️ Kühlleistung", f"{p_kuehlen:.1f} kW")
            st.metric("💰 Kühlung/Jahr", f"{kosten_kuehlen:.0f} €")
        
        # Gesamtkosten
        gesamtkosten = kosten_ventilator + kosten_heizen + kosten_kuehlen + kosten_zusatz
        
        st.markdown("### 💯 Gesamtkosten")
        st.metric("", f"{gesamtkosten:.0f} €/Jahr", 
                 delta=f"+{kosten_zusatz:.0f} € Zusatzkosten" if kosten_zusatz > 0 else None)
        
        # Spezifische Kosten
        spez_kosten = gesamtkosten / (volumenstrom * jahresstunden / 1000)
        st.metric("Spezifische Kosten", f"{spez_kosten:.3f} €/(m³·h)")

with tab2:
    st.subheader("🔄 RLT-Prozess Schritt-für-Schritt")
    
    if prozess['schritte']:
        for i, schritt in enumerate(prozess['schritte']):
            with st.container():
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    st.write(f"**{schritt['punkt']}**")
                with col2:
                    st.write(f"🌡️ {schritt['temperatur']:.1f}°C")
                with col3:
                    st.write(f"💧 {schritt['rel_feuchte']:.1f}% rF")
                with col4:
                    st.write(f"⚡ {schritt['enthalpie']:.0f} J/kg")
                
                if i < len(prozess['schritte']) - 1:
                    st.write("⬇️")
    
    # Aktive Prozesse anzeigen
    st.markdown("### 🔧 Aktive Prozesse:")
    if wrg_wirkungsgrad > 0:
        st.success(f"✅ Wärmerückgewinnung: {wrg_wirkungsgrad*100:.0f}% ({wrg_typ})")
    if betriebsmodi['entfeuchten']:
        st.info("✅ Entfeuchtung: Kühlung → Kondensation → Nachheizung")
    if betriebsmodi['heizen']:
        st.warning("✅ Heizung erforderlich")
    if betriebsmodi['kuehlen'] and not betriebsmodi['entfeuchten']:
        st.info("✅ Kühlung erforderlich")

with tab3:
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("💰 Kostenverteilung")
        
        # Kreisdiagramm mit Plotly
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
        
        if kosten_zusatz > 0:
            kosten_data.append(kosten_zusatz)
            labels_data.append("Wartung/Filter")
            colors.append("#96CEB4")
        
        if kosten_data:
            fig = go.Figure(data=[go.Pie(
                labels=labels_data,
                values=kosten_data,
                hole=0.3,
                marker=dict(colors=colors)
            )])
            fig.update_traces(textposition='inside', textinfo='percent+label')
            fig.update_layout(height=400, showlegend=True)
            st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        st.subheader("⚡ Leistungsverteilung")
        
        leistung_data = []
        leistung_labels = []
        
        if p_ventilator > 0:
            leistung_data.append(p_ventilator)
            leistung_labels.append("Ventilator")
        
        if p_heizen > 0:
            leistung_data.append(p_heizen)
            leistung_labels.append("Heizung")
        
        if p_kuehlen > 0:
            leistung_data.append(p_kuehlen)
            leistung_labels.append("Kühlung")
        
        if leistung_data:
            fig2 = go.Figure(data=[go.Bar(
                x=leistung_labels,
                y=leistung_data,
                marker_color=['#FF6B6B', '#4ECDC4', '#45B7D1']
            )])
            fig2.update_layout(
                title="Elektrische und thermische Leistung [kW]",
                height=400,
                yaxis_title="Leistung [kW]"
            )
            st.plotly_chart(fig2, use_container_width=True)

with tab4:
    st.subheader("📄 Professional Report")
    
    if st.button("📄 Detaillierten PDF-Report generieren", type="primary"):
        # Professional PDF Report
        buffer = BytesIO()
        p = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        
        # Header
        p.setFont("Helvetica-Bold", 16)
        p.drawString(50, height-50, "RLT-Anlagen Kostenanalyse Professional")
        p.setFont("Helvetica", 10)
        p.drawString(50, height-70, f"Erstellt am: {pd.Timestamp.now().strftime('%d.%m.%Y %H:%M')}")
        
        # Anlagendaten
        y_pos = height - 120
        p.setFont("Helvetica-Bold", 12)
        p.drawString(50, y_pos, "Anlagendaten:")
        y_pos -= 20
        
        p.setFont("Helvetica", 10)
        anlagendaten = [
            f"Volumenstrom: {volumenstrom:,.0f} m³/h",
            f"Betriebszeit: {betriebsstunden_tag} h/Tag × {betriebstage_jahr} Tage = {jahresstunden:,.0f} h/Jahr",
            f"Teillastfaktor: {teillast_faktor*100:.0f}%",
            f"SFP: {sfp:.1f} W/(m³/h)"
        ]
        
        for item in anlagendaten:
            p.drawString(70, y_pos, item)
            y_pos -= 15
        
        # Klimadaten
        y_pos -= 20
        p.setFont("Helvetica-Bold", 12)
        p.drawString(50, y_pos, "Klimabedingungen:")
        y_pos -= 20
        
        p.setFont("Helvetica", 10)
        klimadaten = [
            f"Außenluft: {temp_aussen:.1f}°C, {feuchte_aussen:.1f}% rF",
            f"Zuluft-Soll: {temp_zuluft:.1f}°C" + (f", {feuchte_zuluft_rel:.1f}% rF" if entfeuchten else ""),
            f"WRG: {wrg_typ} ({wrg_wirkungsgrad*100:.0f}%)" if wrg_vorhanden else "WRG: Keine"
        ]
        
        for item in klimadaten:
            p.drawString(70, y_pos, item)
            y_pos -= 15
        
        # Ergebnisse
        y_pos -= 20
        p.setFont("Helvetica-Bold", 12)
        p.drawString(50, y_pos, "Berechnungsergebnisse:")
        y_pos -= 20
        
        p.setFont("Helvetica", 10)
        ergebnisse = [
            f"Ventilatorleistung: {p_ventilator:.1f} kW → {kosten_ventilator:.0f} €/Jahr",
            f"Heizleistung: {p_heizen:.1f} kW → {kosten_heizen:.0f} €/Jahr",
            f"Kühlleistung: {p_kuehlen:.1f} kW → {kosten_kuehlen:.0f} €/Jahr",
            f"Zusatzkosten: {kosten_zusatz:.0f} €/Jahr",
            "",
            f"GESAMTKOSTEN: {gesamtkosten:.0f} €/Jahr",
            f"Spezifische Kosten: {spez_kosten:.3f} €/(m³·h)"
        ]
        
        for item in ergebnisse:
            if item.startswith("GESAMTKOSTEN"):
                p.setFont("Helvetica-Bold", 10)
            p.drawString(70, y_pos, item)
            p.setFont("Helvetica", 10)
            y_pos -= 15
        
        p.save()
        buffer.seek(0)
        
        st.download_button(
            label="📥 Professional PDF Report herunterladen",
            data=buffer,
            file_name=f"RLT_Analyse_Professional_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.pdf",
            mime="application/pdf"
        )

# Footer
st.markdown("---")
st.markdown("*RLT-Anlagen Kostenanalyse Professional v2.0 | Thermodynamisch korrekte Berechnung*")
