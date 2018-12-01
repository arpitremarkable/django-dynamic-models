import pytest
from django.db import connection, models
from dynamic_models import signals as dm_signals

from dynamic_models import exceptions
from .models import ModelSchema, FieldSchema
from .conftest import (
    db_table_exists, db_table_has_field, db_field_allows_null, is_registered
)



@pytest.fixture
def model_schema(request, db):
    """Creates and yields an instance of the model schema.
    
    A database table should be created when it is loaded and cleaned up after
    the test.
    """
    instance = ModelSchema.objects.create(name='simple model')
    request.addfinalizer(instance.delete)
    return instance

@pytest.fixture
def model_schema_no_delete(db):
    """Creates a model schema instance that must be manually cleaned up.
    
    Use this fixture to test for correct deletion behavior.
    """
    return ModelSchema.objects.create(name='simple model')

@pytest.fixture
def int_field_schema(db):
    """Creates an integer field schema instance.
    
    Fixture does not add a column to any table until it is added to a model
    schema instance with the `model_schema.add_field` method.
    """
    return FieldSchema.objects.create(
        name='simple integer',
        data_type=FieldSchema.DATA_TYPES.int
    )

@pytest.fixture
def char_field_schema(db):
    """Creates field schema instance with the character data type.
    
    Fixture does not add a column to any table until it is added to a model
    schema instance with the `model_schema.add_field` method.
    """
    return FieldSchema.objects.create(
        name='simple character',
        data_type=FieldSchema.DATA_TYPES.char
    )


def test_subclassed_models_have_base_fields():
    """`AbstractModelSchema` subclasses should inherit the necessary fields."""
    assert ModelSchema._meta.get_field('name')
    assert ModelSchema._meta.get_field('modified')
    assert FieldSchema._meta.get_field('name')
    assert FieldSchema._meta.get_field('data_type')

def test_adding_model_schema_creates_db_table(model_schema):
    """Should create database table for the model schema instance."""
    assert db_table_exists(model_schema.table_name)

def test_adding_model_schema_registers_dynamic_model(model_schema):
    """Dynamic model should exist in Django's app registry."""
    assert is_registered(model_schema.get_dynamic_model())

def test_dynamic_model_is_django_model(model_schema):
    """Dynamic model classes should subclass from Django's Model class."""
    assert issubclass(model_schema.get_dynamic_model(), models.Model)

def test_deleting_model_schema_deletes_db_table(model_schema_no_delete):
    """Database table of dynamic models should be dropped on delete."""
    table = model_schema_no_delete.table_name
    assert db_table_exists(table)
    model_schema_no_delete.delete()
    assert not db_table_exists(table)

def test_deleting_model_schema_unregisters_dynamic_model(model_schema_no_delete):
    """Dynamic model should be unregistered when its schema is deleted."""
    model = model_schema_no_delete.get_dynamic_model()
    assert is_registered(model)
    model_schema_no_delete.delete()
    assert not is_registered(model)

def test_adding_field_schema_adds_db_fields(model_schema, int_field_schema):
    """Adding field schema to a model schema should create database field."""
    assert not db_table_has_field(
        model_schema.table_name,
        int_field_schema.column_name
    )
    model_schema.add_field(int_field_schema)
    assert db_table_has_field(
        model_schema.table_name,
        int_field_schema.column_name
    )

def test_removing_field_schema_removes_db_fields(model_schema, int_field_schema):
    """Removing field schema from a model schema shoudl remove database field."""
    model_schema.add_field(int_field_schema)
    assert db_table_has_field(
        model_schema.table_name,
        int_field_schema.column_name
    )
    model_schema.remove_field(int_field_schema)
    assert not db_table_has_field(
        model_schema.table_name,
        int_field_schema.column_name
    )

def test_updating_field_updates_db_schema(model_schema, int_field_schema):
    """Updating a `DynamicModelField` should update the database field."""
    model_schema.add_field(int_field_schema, required=True)
    assert not db_field_allows_null(
        model_schema.table_name,
        int_field_schema.column_name
    )
    model_schema.update_field(int_field_schema, required=False)
    assert db_field_allows_null(
        model_schema.table_name,
        int_field_schema.column_name
    )

def test_char_field_requires_max_length(model_schema, char_field_schema):
    """Adding a character field requires a `max_length` to be set.
    
    Raise `InvalidFieldError` when `max_length` is not declared.
    """
    with pytest.raises(exceptions.InvalidFieldError,
            match=char_field_schema.column_name):
        model_schema.add_field(char_field_schema)
    assert model_schema.add_field(char_field_schema, max_length=64)

def test_non_char_fields_cannot_have_max_length(model_schema, int_field_schema):
    """Only character fields should be able to set `max_length`.

    Raise `InvalidFieldError` if a field of a different type attempts to
    configure the `max_length`.
    """
    with pytest.raises(exceptions.InvalidFieldError,
            match=int_field_schema.column_name):
        model_schema.add_field(int_field_schema, max_length=64)

def test_cannot_change_not_required_to_required(model_schema, int_field_schema):
    """Fields cannot change the `required` option from `True` to `False`.

    This would potentially require a data migration. Support for this behavior
    is on the road map.
    """
    null_field = model_schema.add_field(int_field_schema, required=False)
    with pytest.raises(exceptions.NullFieldChangedError,
            match=int_field_schema.column_name):
        null_field.required = True
        null_field.save()

def test_schema_timestamp_updated_on_field_change(model_schema, int_field_schema):
    """Model schema's timestamp should be updated when a field is changed."""
    field = model_schema.add_field(int_field_schema, required=True)
    initial_time = model_schema.modified
    field.required = False
    field.save()
    model_schema.refresh_from_db()
    assert model_schema.modified > initial_time 

def test_CRUD_dynamic_models_instances(model_schema, int_field_schema):
    """Dynamic models should be able to create, update, and destroy instances."""
    model_schema.add_field(int_field_schema)
    model = model_schema.get_dynamic_model()
    field_name = int_field_schema.column_name

    instance = model.objects.create(**{field_name: 1})
    assert instance, "instance not created"
    
    assert model.objects.get(pk=instance.pk), "instance not retrieved"

    model.objects.update(**{field_name: 2})
    instance.refresh_from_db()
    assert getattr(instance, field_name) == 2, "instance not updated"
    
    pk = instance.pk
    instance.delete()
    with pytest.raises(model.DoesNotExist):
        model.objects.get(pk=pk)

def test_cannot_save_with_outdated_model(model_schema, int_field_schema):
    """Outdated model schema cannot be used to insert new records.
    
    Raise `OutdatedModelError` when this is attempted.
    """
    model = model_schema.get_dynamic_model()
    model_schema.add_field(int_field_schema, required=False)
    with pytest.raises(exceptions.OutdatedModelError,
            match=model_schema.model_name):
        model.objects.create()
