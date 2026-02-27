import os
import logging
from typing import Dict, Optional, Any
from datetime import datetime
from google.adk.agents.llm_agent import LlmAgent, Agent
from dotenv import load_dotenv

from .database import (
    search_specialty_availability,
    create_appointment,
    cancel_appointment,
    get_appointment_by_id
)

load_dotenv()
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

GEMINI_MODEL = os.getenv("MODEL", "gemini-1.5-flash")

# ==================== VALIDADORES ====================

def validate_cpf(cpf: str) -> bool:
    """Valida se CPF tem 11 dígitos"""
    if not cpf:
        return False
    cpf_clean = str(cpf).replace(".", "").replace("-", "").strip()
    return len(cpf_clean) == 11 and cpf_clean.isdigit()

def validate_date_of_birth(date_str: str) -> bool:
    """Valida data de nascimento em formato DD/MM/YYYY ou YYYY-MM-DD"""
    if not date_str:
        return False
    try:
        try:
            datetime.strptime(str(date_str).strip(), "%d/%m/%Y")
            return True
        except ValueError:
            datetime.strptime(str(date_str).strip(), "%Y-%m-%d")
            return True
    except:
        return False

def convert_date_to_iso(date_str: str) -> str:
    """Converte data DD/MM/YYYY para YYYY-MM-DD"""
    if not date_str:
        return None
    try:
        date_obj = datetime.strptime(str(date_str).strip(), "%d/%m/%Y")
        return date_obj.strftime("%Y-%m-%d")
    except ValueError:
        return str(date_str).strip()

# ==================== TOOLS ====================

def schedule_search(specialty: str) -> Dict[str, Any]:
    """
    Busca disponibilidade de clínicas, médicos e horários para uma determinada especialidade.
    IMPORTANTE: Esta ferramenta DEVE ser chamada quando o paciente mencionar uma especialidade.
    """
    logger.info(f"========== SCHEDULE_SEARCH INICIADO ==========")

    specialty = str(specialty).strip() if specialty else ""
    logger.info(f"Especialidade solicitada: '{specialty}'")

    if not specialty:
        logger.warning("Especialidade vazia ou inválida recebida")
        return {"status": "error", "message": "Por favor, informe uma especialidade válida."}

    try:
        logger.debug(f"Chamando search_specialty_availability com: '{specialty}'")
        results = search_specialty_availability(specialty)

        logger.info(f"Resposta do banco de dados recebida")
        logger.info(f"Número de resultados encontrados: {len(results) if results else 0}")

        if not results:
            logger.warning(f"Nenhuma disponibilidade encontrada para '{specialty}'")
            return {
                "status": "not_found",
                "message": f"Não encontrei disponibilidade para '{specialty}' no momento.",
                "data": []
            }

        logger.info(f"✓ Sucesso! {len(results)} resultado(s) encontrado(s)")

        return {
            "status": "success",
            "specialty": specialty,
            "total_results": len(results),
            "data": results,
            "message": f"Encontrei disponibilidade em {len(results)} clinica(s) para {specialty}."
        }

    except Exception as e:
        logger.error(f"❌ EXCEÇÃO em schedule_search: {type(e).__name__}: {str(e)}")
        return {
            "status": "error",
            "message": f"Erro ao buscar: {str(e)}",
            "data": []
        }
    finally:
        logger.info(f"========== SCHEDULE_SEARCH FINALIZADO ==========\n")


def schedule_appointment(
    patient_name: str,
    patient_cpf: str,
    patient_date_of_birth: str,
    doctor_id: int,
    slot_id: int,
    clinic_id: str,
    patient_email: str = None,
    patient_phone: str = None,
    insurance_type: str = None,
    insurance_plan_id: int = None
) -> Dict[str, Any]:
    """
    Registra um agendamento no banco de dados e bloqueia o horário.
    IMPORTANTE: Chame esta ferramenta APÓS coletar todos os dados do paciente.
    """
    logger.info(f"========== SCHEDULE_APPOINTMENT INICIADO ==========")

    try:
        patient_name = str(patient_name).strip() if patient_name else ""
        patient_cpf = str(patient_cpf).strip() if patient_cpf else ""
        patient_date_of_birth = str(patient_date_of_birth).strip() if patient_date_of_birth else ""
        patient_email = str(patient_email).strip() if patient_email else ""
        patient_phone = str(patient_phone).strip() if patient_phone else ""
        insurance_type = str(insurance_type).strip().upper() if insurance_type else None

        logger.info(f"Paciente: {patient_name} | CPF: {patient_cpf}")

        if not validate_cpf(patient_cpf):
            logger.warning(f"❌ CPF inválido: {patient_cpf}")
            return {
                "status": "error",
                "message": f"CPF inválido: {patient_cpf}. Por favor, informe 11 dígitos sem formatação."
            }

        if not validate_date_of_birth(patient_date_of_birth):
            logger.warning(f"❌ Data de nascimento inválida: {patient_date_of_birth}")
            return {
                "status": "error",
                "message": f"Data de nascimento inválida: {patient_date_of_birth}. Use formato DD/MM/YYYY."
            }

        patient_date_of_birth_iso = convert_date_to_iso(patient_date_of_birth)
        logger.info(f"Data convertida de '{patient_date_of_birth}' para '{patient_date_of_birth_iso}'")

        if not patient_name or len(patient_name) < 3:
            return {"status": "error", "message": "Nome do paciente inválido ou incompleto."}

        if not all([doctor_id, slot_id, clinic_id]):
            return {"status": "error", "message": "Dados de médico, horário ou clínica faltando."}

        try:
            doctor_id = int(doctor_id)
            slot_id = int(slot_id)
            clinic_id = str(clinic_id).strip() if clinic_id else None
            if insurance_plan_id:
                insurance_plan_id = int(insurance_plan_id)
            logger.info(f"✓ Conversão de IDs para BIGINT bem-sucedida")
        except (ValueError, TypeError) as e:
            logger.error(f"❌ Erro ao converter IDs: {e}")
            return {"status": "error", "message": "Erro ao processar IDs. Por favor, tente novamente."}

        valid_types = ['PARTICULAR', 'HEALTH_PLAN']
        if insurance_type:
            insurance_type = insurance_type.upper()
            if insurance_type not in valid_types:
                insurance_type = 'PARTICULAR'
        else:
            insurance_type = 'PARTICULAR'

        logger.info(f"✓ Insurance type validado: {insurance_type}")

        patient_data = {
            'name': patient_name,
            'cpf': patient_cpf.replace(".", "").replace("-", ""),
            'date_of_birth': patient_date_of_birth_iso,
            'email': patient_email or f"paciente_{patient_cpf}@clinica.com",
            'phone': patient_phone or "",
            'insurance_type': insurance_type
        }

        logger.debug(f"Dados do paciente preparados: {patient_data}")
        logger.info(f"Chamando create_appointment no banco de dados...")

        result = create_appointment(
            patient_data=patient_data,
            doctor_id=doctor_id,
            slot_id=slot_id,
            clinic_id=clinic_id,
            insurance_type=insurance_type,
            insurance_plan_id=insurance_plan_id,
            notes=None
        )

        logger.debug(f"Resposta do banco de dados: {result}")

        if result['status'] == 'success':
            appointment_id = result.get('appointment_id')
            logger.info(f"✓ Agendamento criado com sucesso! ID: {appointment_id}")
            appointment_details = get_appointment_by_id(appointment_id)
            return {
                "status": "success",
                "appointment_id": appointment_id,
                "message": "✓ Agendamento confirmado com sucesso!",
                "details": appointment_details
            }
        else:
            error_msg = result.get('message', 'Erro desconhecido')
            logger.warning(f"❌ Falha ao criar agendamento: {error_msg}")
            return result

    except Exception as e:
        logger.error(f"❌ EXCEÇÃO em schedule_appointment: {type(e).__name__}: {str(e)}")
        logger.exception("Stack trace completo:")
        return {"status": "error", "message": f"Erro ao confirmar agendamento: {str(e)}"}
    finally:
        logger.info(f"========== SCHEDULE_APPOINTMENT FINALIZADO ==========\n")


def cancel_appointment_tool(appointment_id: int, reason: str = None) -> Dict[str, Any]:
    """
    Cancela um agendamento e libera o horário.
    """
    logger.info(f"========== CANCEL_APPOINTMENT INICIADO ==========")

    try:
        appointment_id = int(appointment_id) if appointment_id else None
        reason = str(reason).strip() if reason else None

        logger.info(f"ID do agendamento: {appointment_id}")

        if not appointment_id:
            return {"status": "error", "message": "ID do agendamento é obrigatório."}

        logger.info(f"Chamando cancel_appointment no banco de dados...")
        result = cancel_appointment(appointment_id, reason)

        if result['status'] == 'success':
            logger.info(f"✓ Agendamento cancelado com sucesso!")
        else:
            logger.warning(f"❌ Falha ao cancelar: {result.get('message')}")

        return result

    except (ValueError, TypeError) as e:
        logger.error(f"❌ Erro ao converter appointment_id: {e}")
        return {"status": "error", "message": f"ID do agendamento inválido: {str(e)}"}
    except Exception as e:
        logger.error(f"❌ EXCEÇÃO em cancel_appointment: {type(e).__name__}: {str(e)}")
        logger.exception("Stack trace completo:")
        return {"status": "error", "message": f"Erro ao cancelar: {str(e)}"}
    finally:
        logger.info(f"========== CANCEL_APPOINTMENT FINALIZADO ==========\n")

# ==================== AGENTS ====================

schedule_agent = LlmAgent(
    model=GEMINI_MODEL,
    name="agendador_virtual",
    description="Agente especialista em buscar e confirmar agendamentos de consultas médicas.",
    instruction="""
    # VOCÊ É O AGENDADOR VIRTUAL

    Sua missão é ajudar pacientes a agendar consultas médicas usando as ferramentas disponíveis.

    ## FLUXO OBRIGATÓRIO:

    ### PASSO 1: Buscar Disponibilidade
    - Quando o paciente mencionar uma especialidade (ex: "cardiologia", "oftalmologia"), 
      IMEDIATAMENTE use a ferramenta schedule_search com essa especialidade.
    - Não invente dados! Sempre use schedule_search para buscar informações reais.
    - Apresente os resultados de forma clara e organizada.
    - Mostre: Clínica, Médico, Especialidade, Data, Hora, Valor da Consulta

    ### PASSO 2: Coletar Dados do Paciente
    Antes de confirmar um agendamento, SEMPRE colete:
    - Nome completo
    - CPF (11 dígitos exatos, sem formatação: 12345678900)
    - Data de nascimento (formato DD/MM/YYYY: 15/03/1990)
    - Email (opcional)
    - Telefone (opcional)

    ### PASSO 3: Confirmar Detalhes EXPLICITAMENTE
    Repita TODOS os dados ao paciente e peça confirmação EXPLÍCITA antes de prosseguir:
    - "Confirma que seu nome é [NOME]?"
    - "Confirma que seu CPF é [CPF]?"
    - "Data de nascimento: [DATA]?"
    - Aguarde resposta explicitamente positiva!

    ### PASSO 4: Registrar Agendamento
    APÓS confirmação EXPLÍCITA, use schedule_appointment COM TODOS ESTES PARÂMETROS:
    - patient_name, patient_cpf, patient_date_of_birth, doctor_id, slot_id, clinic_id

    ## REGRAS CRÍTICAS:
    1. SEMPRE use schedule_search quando uma especialidade é mencionada
    2. NUNCA invente dados - use sempre dados do banco
    3. SEMPRE confirme dados ANTES de usar schedule_appointment
    4. SEMPRE mostre: clínica, médico, especialidade, data, hora, valor
    5. NUNCA mostre IDs técnicos diretamente ao paciente
    6. CPF DEVE TER 11 DÍGITOS EXATOS (ex: 12345678900)
    7. Data DEVE SER DD/MM/YYYY (ex: 15/03/1990)
    8. doctor_id, slot_id, clinic_id SÃO OBRIGATÓRIOS
    9. SEMPRE inclua patient_name, patient_cpf, patient_date_of_birth
    10. Mantenha tom profissional e educado
    11. NUNCA responda "agendamento feito" sem chamar a ferramenta schedule_appointment

    ## SE NÃO HOUVER DISPONIBILIDADE:
    - Informe com empatia
    - Sugira outras especialidades ou datas

    ## SE O PACIENTE PEDIR CANCELAMENTO:
    - Use cancel_appointment_tool com o ID do agendamento
    - Peça confirmação EXPLÍCITA antes de cancelar
    """,
    tools=[schedule_search, schedule_appointment, cancel_appointment_tool]
)


clinic_root_agent = Agent(
    model=GEMINI_MODEL,
    name="clinic_appointment_system",
    description="Sistema de agendamento de consultas médicas com persistência real no banco de dados.",
    instruction="""
    # VOCÊ É O COORDENADOR DO SISTEMA DE AGENDAMENTO

    Sua função é atender pacientes profissionalmente e garantir que os dados sejam coletados corretamente.

    ## FLUXO PADRÃO:

    1. Cumprimentar: Dê boas-vindas educadamente
    2. Entender necessidade: Pergunte qual especialidade o paciente precisa
    3. Validar dados básicos: 
       - Nome completo 
       - CPF (11 dígitos: 12345678900) - SEM PONTOS OU HÍFEN
       - Data de nascimento (formato DD/MM/YYYY: 15/03/1990)
    4. Confirmar informações: REPITA TUDO e peça confirmação EXPLÍCITA
    5. DELEGAR IMEDIATAMENTE: Após confirmação explícita, repasse para agendador_virtual

    ## DADOS OBRIGATÓRIOS:
    - Nome completo (mínimo 3 caracteres, sem abreviações)
    - CPF (EXATAMENTE 11 dígitos: 12345678900)
    - Data de nascimento (DD/MM/YYYY: 15/03/1990)
    - Especialidade desejada

    ## QUALIDADE DO ATENDIMENTO:
    - Fale no idioma Português do Brasil
    - SEMPRE confirme dados ANTES de repassar
    - Se CPF não tiver 11 dígitos, solicite novamente
    - Se data não estiver em DD/MM/YYYY, peça novamente
    - NUNCA invente informações
    - Se falta algum dado, solicite explicitamente
    - Mantenha tom profissional e amigável
    - Respeite a privacidade do paciente
    - Seja direto, não fale mais que o necessário
    """,
    sub_agents=[schedule_agent],
    tools=[]
)

# ADK exige esta variável para encontrar o agente raiz
root_agent = clinic_root_agent


if __name__ == "__main__":
    import asyncio

    logger.info("Iniciando Clinic Agent (ADK + Vertex AI Gemini)...")

    async def run_agent():
        logger.info("✓ Agent iniciado com sucesso!")
        while True:
            await asyncio.sleep(1)

    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        logger.info("Agent parado pelo usuário")
    except Exception as e:
        logger.error(f"Erro fatal: {e}")
