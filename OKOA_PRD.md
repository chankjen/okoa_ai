PRODUCT REQUIREMENTS DOCUMENT (PRD)
===================================

**Version:** 1.0 (MVP)  
**Product Manager:** ZeTA

### **1. Executive Summary**

OKOA AI is a WhatsApp-based conversational agent designed to provide accessible, stigma-free mental health and addiction support to Kenyan youth. The MVP focuses on anonymous daily check-ins, empathetic AI responses, and safe escalation to human counselors.

### **2. User Personas**

* **Juma (19):** A university student struggling with peer pressure and substance abuse. Needs anonymous, non-judgmental advice late at night.
* **Amina (24):** In early recovery from alcohol addiction. Needs daily motivation, relapse prevention strategies, and help finding a support group.
* **Dr. Ochieng (Counselor):** A busy NGO counselor who needs a dashboard to monitor high-risk AI-flagged users and intervene quickly.

### **3. User Stories & MVP Features**

**Epic 1: Onboarding & Anonymity**

* _As a user,_ I want to start chatting without creating an account or sharing my real name, so that I remain anonymous and safe from stigma.
* _Feature:_ Zero-friction WhatsApp opt-in. Auto-generates a secure, anonymous User ID.

**Epic 2: Conversational Support**

* _As a user,_ I want to express my feelings in Sheng or Swahili and receive empathetic, culturally relevant responses.
* _Feature:_ Llama 3-powered conversational agent trained on localized mental health datasets.

**Epic 3: Daily Check-ins & Mood Tracking**

* _As a user,_ I want to log my daily mood and triggers easily.
* _Feature:_ Automated daily prompts (e.g., "Vipi leo? How are you feeling today?") with quick-reply emoji/text options.

**Epic 4: Safety & Human Escalation (Crucial)**

* _As a user,_ I want to be connected to a human if I am in a crisis.
* _Feature:_ Real-time sentiment analysis. If risk score > 85%, the AI pauses, shows a crisis helpline (e.g., 1199), and alerts the Counselor Dashboard.

**Epic 5: Resource Matching**

* _As a user,_ I want to find a nearby rehab or support group.
* _Feature:_ Location-based directory of verified NGOs and rehabs.

### **4. Non-Functional Requirements**

* **Privacy:** 100% anonymous. No PII (Personally Identifiable Information) stored in the LLM context.
* **Accessibility:** Must work on low-end smartphones and 2G/3G networks via WhatsApp text (no heavy media).
* **Latency:** AI response time must be under 5 seconds to maintain conversational flow.

### **5. Success Metrics (KPIs)**

* **Engagement:** Daily Active Users (DAU), average session length, Day-7/Day-30 retention.
* **Safety:** Time-to-escalation for high-risk users (Target: < 2 minutes).
* **Clinical:** User-reported reduction in cravings/stress (measured via weekly micro-surveys).
