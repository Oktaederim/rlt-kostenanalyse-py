import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from io import BytesIO
import base64
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import psychrolib

# Seiten-Konfiguration
st.set_page_config(
    page_title="RLT-Anlagen Kostenanalyse",
    page_icon="🏭",
    layout="wide"
)

# Haupttitel
st.title("🏭 RLT-Anlagen Kostenanalyse")
st.markdown("---")

# Sidebar für Eingaben
with st.sidebar:
    st.header("Anlagendaten")
    
    # Grunddaten
    volumenstrom = st.number_input("Zuluft-Volumenstrom [m³/h]", value=10000, min_value=100)
    betriebsstunden_tag = st.number_input("Betriebsstunden/Tag [h]", value=12, min_value=1, max_value=24)
    betriebstage_jahr = st.number_input("Betriebstage/Jahr", value=250, min_value=1, max_value=365)
    
    st.subheader("Klimadaten")
    temp_aussen = st.slider("Außentemperatur [°C]", -20, 40, 5)
    feuchte_aussen = st.slider("Außenluft rel. Feuchte [%]", 30, 90, 60)
    temp_zuluft = st.slider("Zuluft-Solltemperatur [°C]", 16, 26, 20)
    
    st.subheader("Betriebsmodi")
    heizen = st.checkbox("Heizen", True)
    kuehlen = st.checkbox("Kühlen", False)
    entfeuchten = st.checkbox("Entfeuchten", False)
    
    if entfeuchten:
        feuchte_zuluft = st.slider("Zuluft rel. Feuchte [%]", 30, 70, 50)
    
    st.subheader("Wärmerückgewinnung")
    wrg_vorhanden = st.checkbox("WRG vorhanden", True)
    if wrg_vorhanden:
        wrg_wirkungsgrad = st.slider("WRG-Wirkungsgrad [%]", 50, 85, 70) / 100
    else:
        wrg_wirkungsgrad = 0

# Hauptbereich mit Tabs
tab1, tab2, tab3 = st.tabs(["📊 Berechnung", "📈 Diagramme", "📄 Report"])

with tab1:
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Technische Parameter")
        sfp = st.number_input("Spez. Ventilatorleistung SFP [W/(m³/h)]", value=2.5, min_value=0.5, max_value=6.0)
        
        st.subheader("Energiepreise")
        preis_strom = st.number_input("Strompreis [€/kWh]", value=0.25, format="%.3f")
        preis_waerme = st.number_input("Fernwärmepreis [€/kWh]", value=0.08, format="%.3f")
        preis_kaelte = st.number_input("Kältepreis [€/kWh]", value=0.15, format="%.3f")
    
    with col2:
        st.subheader("Berechnungsergebnisse")
        
        # Ventilatorleistung
        p_ventilator = volumenstrom * sfp / 1000  # kW
        kosten_ventilator = p_ventilator * betriebsstunden_tag * betriebstage_jahr * preis_strom
        
        # Vereinfachte Heiz-/Kühlleistung (ohne psychrometrische Berechnung)
        if heizen and temp_zuluft > temp_aussen:
            delta_t_heiz = temp_zuluft - temp_aussen
            if wrg_vorhanden:
                delta_t_heiz = delta_t_heiz * (1 - wrg_wirkungsgrad)
            
            p_heizen = volumenstrom * delta_t_heiz * 1.2 * 1.005 / 3600  # kW (vereinfacht)
            kosten_heizen = p_heizen * betriebsstunden_tag * betriebstage_jahr * preis_waerme
        else:
            p_heizen = 0
            kosten_heizen = 0
        
        if kuehlen and temp_zuluft < temp_aussen:
            delta_t_kuehlen = temp_aussen - temp_zuluft
            p_kuehlen = volumenstrom * delta_t_kuehlen * 1.2 * 1.005 / 3600  # kW
            kosten_kuehlen = p_kuehlen * betriebsstunden_tag * betriebstage_jahr * preis_kaelte
        else:
            p_kuehlen = 0
            kosten_kuehlen = 0
        
        # Ergebnisse anzeigen
        st.metric("Ventilatorleistung", f"{p_ventilator:.1f} kW")
        st.metric("Heizleistung", f"{p_heizen:.1f} kW")
        st.metric("Kühlleistung", f"{p_kuehlen:.1f} kW")
        
        st.markdown("### Jährliche Kosten")
        st.metric("Ventilator", f"{kosten_ventilator:.0f} €/a")
        st.metric("Heizung", f"{kosten_heizen:.0f} €/a")
        st.metric("Kühlung", f"{kosten_kuehlen:.0f} €/a")
        
        gesamtkosten = kosten_ventilator + kosten_heizen + kosten_kuehlen
        st.metric("Gesamtkosten", f"{gesamtkosten:.0f} €/a", delta=None)

with tab2:
    st.subheader("Kostenverteilung")
    
    # Kreisdiagramm
    labels = ['Ventilator', 'Heizung', 'Kühlung']
    sizes = [kosten_ventilator, kosten_heizen, kosten_kuehlen]
    sizes = [max(0, x) for x in sizes]  # Negative Werte vermeiden
    
    fig, ax = plt.subplots()
    ax.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=90)
    ax.axis('equal')
    st.pyplot(fig)

with tab3:
    st.subheader("PDF-Report erstellen")
    
    if st.button("📄 PDF generieren"):
        # Einfacher PDF-Report
        buffer = BytesIO()
        p = canvas.Canvas(buffer, pagesize=A4)
        
        # PDF Inhalt
        p.drawString(100, 750, "RLT-Anlagen Kostenanalyse")
        p.drawString(100, 720, f"Volumenstrom: {volumenstrom} m³/h")
        p.drawString(100, 700, f"Betriebsstunden: {betriebsstunden_tag} h/Tag")
        p.drawString(100, 680, f"Ventilatorleistung: {p_ventilator:.1f} kW")
        p.drawString(100, 660, f"Heizleistung: {p_heizen:.1f} kW")
        p.drawString(100, 640, f"Gesamtkosten: {gesamtkosten:.0f} €/Jahr")
        
        p.save()
        buffer.seek(0)
        
        st.download_button(
            label="📥 PDF herunterladen",
            data=buffer,
            file_name="rlt_analyse.pdf",
            mime="application/pdf"
        )

# Footer
st.markdown("---")
st.markdown("*RLT-Anlagen Kostenanalyse v1.0*")
