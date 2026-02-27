import psycopg2
from psycopg2.extras import RealDictCursor
import os
from collections import defaultdict
from dotenv import load_dotenv
from datetime import date, datetime
import logging

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_db_connection():
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT")
    )

def search_specialty_availability(specialty):
    """
    Busca clinicas, medicos dessa especialidade e seus horarios livres.
    IMPORTANTE: clinic_id é UUID, doctor_id/slot_id são BIGINT!
    """
    logger.info(f"DATABASE: Iniciando busca para especialidade '{specialty}'")
    
    conn = None
    cur = None
    
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Query CORRIGIDA com nomes reais das colunas
        query = """
            SELECT 
                c.id as clinic_id,
                c.legal_name as clinic_name,
                c.address as clinic_address,
                c.city,
                c.state,
                c.phone as clinic_phone,
                CAST(d.id AS BIGINT) as doctor_id,
                d.name as doctor_name,
                d.specialty,
                CAST(a.id AS BIGINT) as slot_id,
                a.date as appointment_date,
                a.time as appointment_time,
                a.is_available
            FROM clinics c
            JOIN doctors d ON c.id = d.clinic_id
            JOIN available_slots a ON d.id = a.doctor_id AND c.id = a.clinic_id
            WHERE LOWER(TRIM(d.specialty)) LIKE LOWER(TRIM(%s))
            AND a.is_available = true
            AND a.date >= CURRENT_DATE
            ORDER BY a.date ASC, a.time ASC
            LIMIT 20
        """
        
        logger.debug(f"Executando query para especialidade: '{specialty}'")
        cur.execute(query, (f"%{specialty}%",))
        results = cur.fetchall()
        
        logger.info(f"✓ Query executada! {len(results) if results else 0} resultado(s)")
        
        if results:
            logger.info(f"Resultados encontrados:")
            for r in results:
                logger.debug(f"  - Clínica: {r.get('clinic_name')}, Médico: {r.get('doctor_name')}")
                logger.debug(f"    Data: {r.get('appointment_date')} {r.get('appointment_time')}")
        else:
            logger.warning(f"❌ Nenhuma disponibilidade encontrada para '{specialty}'")
        
        return results if results else []
        
    except Exception as e:
        logger.error(f"❌ Erro em search_specialty_availability: {type(e).__name__}: {str(e)}")
        logger.exception("Stack trace completo:")
        return []
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

def create_appointment(patient_data: dict, doctor_id: int, slot_id: int, clinic_id: str, insurance_type: str = None, insurance_plan_id: int = None, notes: str = None) -> dict:
    """
    Cria um novo agendamento e bloqueia o horario.
    
    Args:
        patient_data: {
            'name': str, 
            'cpf': str, 
            'date_of_birth': str (YYYY-MM-DD),
            'email': str (opcional),
            'phone': str (opcional),
            'insurance_type': str (PARTICULAR ou HEALTH_PLAN)
        }
        doctor_id: ID do medico (BIGINT)
        slot_id: ID do horario disponivel (BIGINT)
        clinic_id: ID da clinica (BIGINT)
        insurance_type: Tipo de seguro (PARTICULAR ou HEALTH_PLAN)
        insurance_plan_id: ID do plano de seguro (BIGINT, opcional)
        notes: Notas adicionais (opcional)
    
    Returns:
        {'status': 'success/error', 'appointment_id': int, 'message': str}
    """
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Garantir tipos corretos e exatos
        doctor_id = int(doctor_id)
        slot_id = int(slot_id)
        clinic_id = str(clinic_id).strip() if clinic_id else None
        if insurance_plan_id:
            insurance_plan_id = int(insurance_plan_id)
        
        logger.info(f"Criando agendamento: doctor_id={doctor_id}, slot_id={slot_id}, clinic_id={clinic_id}")
        
        # 1. Verificar se o paciente existe, senao criar
        patient_id = _get_or_create_patient(cur, patient_data)
        
        # 2. Verificar se o slot ainda esta disponivel
        slot_check = _check_slot_available(cur, slot_id)
        if not slot_check:
            conn.rollback()
            return {
                "status": "error",
                "message": "Este horario nao esta mais disponivel.",
                "appointment_id": None
            }
        
        # 3. Criar o agendamento
        appointment_id = _insert_appointment(
            cur, 
            patient_id, 
            doctor_id, 
            clinic_id, 
            slot_id,
            insurance_type,
            insurance_plan_id,
            notes
        )
        
        # 4. Bloquear o horario (marcar como indisponivel)
        _block_slot(cur, slot_id)
        
        # Commit da transacao
        conn.commit()
        
        logger.info(f"Agendamento criado: ID {appointment_id}")
        
        return {
            "status": "success",
            "appointment_id": appointment_id,
            "message": f"Agendamento confirmado! ID: {appointment_id}"
        }
        
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Erro ao criar agendamento: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"Erro ao agendar: {str(e)}",
            "appointment_id": None
        }
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def _get_or_create_patient(cur, patient_data: dict) -> int:
    """
    Busca um paciente existente ou cria um novo.
    
    Returns:
        patient_id (BIGINT)
    """
    cpf = patient_data.get('cpf')
    
    # Buscar paciente existente
    query_check = "SELECT id FROM patients WHERE cpf = %s"
    cur.execute(query_check, (cpf,))
    result = cur.fetchone()
    
    if result:
        patient_id = int(result['id'])
        logger.info(f"Paciente existente encontrado: ID {patient_id}")
        return patient_id
    
    # Validar insurance_type em MAIÚSCULAS
    valid_types = ['PARTICULAR', 'HEALTH_PLAN']
    insurance_type = patient_data.get('insurance_type', 'PARTICULAR')
    
    if insurance_type:
        insurance_type = str(insurance_type).upper().strip()
        if insurance_type not in valid_types:
            insurance_type = 'PARTICULAR'
    else:
        insurance_type = 'PARTICULAR'
    
    # Criar novo paciente - Cast correto para insurancetype (minúsculas)
    query_insert = """
        INSERT INTO patients (name, cpf, date_of_birth, email, phone, insurance_type)
        VALUES (%s, %s, %s, %s, %s, %s::insurancetype)
        RETURNING id
    """
    
    email = patient_data.get('email') or f"paciente_{cpf}@clinica.com"
    phone = patient_data.get('phone') or ""
    date_of_birth = patient_data.get('date_of_birth')
    
    logger.info(f"Criando novo paciente com insurance_type={insurance_type}")
    
    cur.execute(query_insert, (
        patient_data.get('name'),
        cpf,
        date_of_birth,
        email,
        phone,
        insurance_type
    ))
    
    new_patient_id = int(cur.fetchone()['id'])
    logger.info(f"Paciente criado: ID {new_patient_id}")
    return new_patient_id


def _check_slot_available(cur, slot_id: int) -> bool:
    """
    Verifica se um horario ainda esta disponivel.
    """
    query = "SELECT is_available FROM available_slots WHERE id = %s"
    cur.execute(query, (slot_id,))
    result = cur.fetchone()
    
    is_available = result and result['is_available']
    logger.info(f"Slot {slot_id} disponível: {is_available}")
    return is_available


def _insert_appointment(cur, patient_id: int, doctor_id: int, clinic_id: str, slot_id: int, insurance_type: str, insurance_plan_id: int, notes: str) -> int:
    """
    Insere o agendamento na tabela appointments.
    Status é sempre 'CONFIRMED' em MAIÚSCULAS.
    
    Returns:
        appointment_id (BIGINT)
    """
    # Status SEMPRE em MAIÚSCULAS
    status = 'CONFIRMED'
    
    # Validar insurance_type em MAIÚSCULAS
    valid_types = ['PARTICULAR', 'HEALTH_PLAN']
    if insurance_type:
        insurance_type = str(insurance_type).upper().strip()
        if insurance_type not in valid_types:
            insurance_type = 'PARTICULAR'
    else:
        insurance_type = 'PARTICULAR'
    
    logger.info(f"Inserindo agendamento: status={status}, insurance_type={insurance_type}")
    
    # Cast correto para insurancetype (minúsculas) e appointmentstatus (minúsculas)
    query = """
        INSERT INTO appointments 
        (patient_id, doctor_id, clinic_id, slot_id, appointment_datetime, insurance_type, insurance_plan_id, status, created_at)
        VALUES (%s, %s, %s, %s, (SELECT (date || ' ' || time)::timestamp FROM available_slots WHERE id = %s), %s::insurancetype, %s, %s::appointmentstatus, NOW())
        RETURNING id
    """
    
    cur.execute(query, (
        patient_id,
        doctor_id,
        clinic_id,
        slot_id,
        slot_id,
        insurance_type,
        insurance_plan_id,
        status
    ))
    
    appointment_id = int(cur.fetchone()['id'])
    logger.info(f"Agendamento inserido: ID {appointment_id}, status={status}")
    return appointment_id


def _block_slot(cur, slot_id: int):
    """
    Marca um horario como indisponivel.
    """
    query = "UPDATE available_slots SET is_available = FALSE WHERE id = %s"
    cur.execute(query, (slot_id,))
    logger.info(f"Horario {slot_id} bloqueado")


def cancel_appointment(appointment_id: int, cancellation_reason: str = None) -> dict:
    """
    Cancela um agendamento e libera o horario.
    Status é atualizado para 'CANCELLED' em MAIÚSCULAS.
    
    Returns:
        {'status': 'success/error', 'message': str}
    """
    conn = None
    cur = None
    try:
        appointment_id = int(appointment_id)
        
        conn = get_db_connection()
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 1. Buscar slot_id do agendamento
        query_get_slot = "SELECT slot_id FROM appointments WHERE id = %s"
        cur.execute(query_get_slot, (appointment_id,))
        result = cur.fetchone()
        
        if not result:
            logger.warning(f"Agendamento {appointment_id} não encontrado")
            return {"status": "error", "message": "Agendamento não encontrado"}
        
        slot_id = int(result['slot_id'])
        
        # 2. Atualizar status do agendamento - Cast correto para appointmentstatus (minúsculas)
        status = 'CANCELLED'
        query_update = """
            UPDATE appointments 
            SET status = %s::appointmentstatus, cancelled_at = NOW(), cancellation_reason = %s
            WHERE id = %s
        """
        cur.execute(query_update, (status, cancellation_reason, appointment_id))
        
        # 3. Liberar o horario
        query_free_slot = "UPDATE available_slots SET is_available = TRUE WHERE id = %s"
        cur.execute(query_free_slot, (slot_id,))
        
        conn.commit()
        
        logger.info(f"Agendamento {appointment_id} cancelado com sucesso")
        
        return {
            "status": "success",
            "message": "Agendamento cancelado com sucesso"
        }
        
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Erro ao cancelar agendamento: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"Erro ao cancelar: {str(e)}"
        }
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_appointment_by_id(appointment_id: int) -> dict:
    """
    Busca detalhes de um agendamento especifico.
    Garante que todos os IDs são BIGINT exatos.
    
    Returns:
        dict com todos os detalhes
    """
    conn = None
    cur = None
    try:
        appointment_id = int(appointment_id)
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        query = """
            SELECT 
                a.id,
                a.patient_id,
                a.doctor_id,
                a.clinic_id,
                a.slot_id,
                a.status,
                a.appointment_datetime,
                p.name as patient_name,
                p.cpf as patient_cpf,
                p.email as patient_email,
                p.phone as patient_phone,
                d.name as doctor_name,
                d.specialty,
                d.consultation_price,
                c.legal_name as clinic_name,
                c.phone as clinic_phone,
                c.address as clinic_address,
                c.city as clinic_city,
                a.insurance_type,
                a.insurance_plan_id,
                a.created_at,
                a.confirmed_at,
                a.cancelled_at,
                a.cancellation_reason
            FROM appointments a
            JOIN patients p ON a.patient_id = p.id
            JOIN doctors d ON a.doctor_id = d.id
            JOIN clinics c ON a.clinic_id = c.id
            WHERE a.id = %s
        """
        
        cur.execute(query, (appointment_id,))
        result = cur.fetchone()
        
        if result:
            result_dict = dict(result)
            # Garantir que IDs são BIGINT exatos
            result_dict['id'] = int(result_dict['id'])
            result_dict['patient_id'] = int(result_dict['patient_id'])
            result_dict['doctor_id'] = int(result_dict['doctor_id'])
            result_dict['clinic_id'] = int(result_dict['clinic_id'])
            result_dict['slot_id'] = int(result_dict['slot_id'])
            if result_dict.get('insurance_plan_id'):
                result_dict['insurance_plan_id'] = int(result_dict['insurance_plan_id'])
            
            logger.info(f"Agendamento {appointment_id} recuperado com sucesso")
            return result_dict
        
        logger.warning(f"Agendamento {appointment_id} não encontrado")
        return None
        
    except Exception as e:
        logger.error(f"Erro ao buscar agendamento: {e}", exc_info=True)
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
