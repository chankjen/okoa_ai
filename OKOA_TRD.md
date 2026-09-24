TECHNICAL REQUIREMENTS DOCUMENT (TRD)
=====================================

**Version:** 1.0 (MVP)  
**Lead Engineer:** ZeTA

### **1. System Architecture Overview**

OKOA AI utilizes a microservices architecture. The frontend is the WhatsApp Business API. The backend consists of an API Gateway, a Conversational Engine (LLM), a Sentiment/Risk Engine (PyTorch), and a secure PostgreSQL database.

### **2. Technology Stack**

* **Frontend/Interface:** WhatsApp Business Cloud API (Meta).
* **Backend Framework:** FastAPI (Python) for high-performance async processing.
* **LLM & NLP:** Meta Llama 3 (8B parameter, fine-tuned via QLoRA for low-resource deployment).
* **Sentiment/Risk Analysis:** PyTorch (Custom fine-tuned RoBERTa or similar lightweight transformer for fast inference).
* **Database:** PostgreSQL (User metadata, session logs) + Redis (Session state/caching).
* **Cloud/Hosting:** AWS (EC2 for inference, RDS for DB) or a localized African cloud provider for data sovereignty.

### **3. Data Flow & Pipeline**

1. **Ingestion:** User sends a WhatsApp message -> Meta Webhook triggers FastAPI.
2. **Risk Pre-screening:** Message is passed to the PyTorch Sentiment Model.
   * _If High Risk:_ Flagged for human escalation, routed to Counselor Dashboard via WebSockets.
   * _If Low/Medium Risk:_ Passed to the LLM.
3. **Context Retrieval (RAG):** FastAPI fetches the user's anonymized session history and relevant CBT coping strategies from the vector database (e.g., Pinecone/pgvector).
4. **Generation:** Llama 3 generates a localized, empathetic response using the prompt + context.
5. **Delivery:** Response sent back to Meta API -> Delivered to user's WhatsApp.

### **4. AI/ML Model Specifications**

* **Llama 3 Fine-Tuning:**
  * _Dataset:_ Curated dataset of 10,000+ anonymized mental health conversations translated/adapted into Swahili and Sheng.
  * _Technique:_ QLoRA (Quantized Low-Rank Adaptation) to fine-tune on a single GPU, reducing compute costs.
  * _Guardrails:_ Strict system prompts preventing the AI from prescribing medication or acting as a definitive medical doctor.
* **PyTorch Sentiment Model:**
  * _Task:_ Multi-class classification (Safe, Distressed, Crisis/Suicidal).
  * _Training:_ Trained on localized slang and idioms to detect subtle distress in Sheng (e.g., "nimechoka sana" vs "nataka kuisha").

### **5. Security, Privacy & Compliance**

* **Data Minimization:** The LLM never receives the user's phone number. The mapping of Phone Number <-> Anonymous UUID is kept in a separate, encrypted vault.
* **Encryption:** Data at rest encrypted using AES-256. Data in transit via TLS 1.3.
* **Compliance:** Strict adherence to the **Kenya Data Protection Act (2019)**. Users can request a full data wipe via a simple WhatsApp command (e.g., "Futa data yangu").
* **Audit Trails:** All AI escalations to human counselors are logged with timestamps for clinical accountability.

### **6. Infrastructure & Scaling Strategy**

* **MVP Phase:** Single GPU instance (e.g., AWS g5.xlarge) for Llama 3 inference. Auto-scaling group for the FastAPI backend.
* **Scale Phase:** Implement vLLM or TGI (Text Generation Inference) for optimized LLM batching and throughput. Introduce a load balancer to handle concurrent WhatsApp webhooks during peak hours (e.g., late nights/weekends).
