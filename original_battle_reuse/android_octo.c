/* Load the exported Octo catalog with the original in-process decoder. */

#include "android_runtime.h"

extern void *dlsym(void *library, const char *name);
extern int printf(const char *format, ...);
extern int fflush(void *stream);
extern int strcmp(const char *left, const char *right);
extern int snprintf(char *output, unsigned long size, const char *format, ...);

int initialize_octo_catalog(void *library, const void *octo_image, const char *stage_resource) {
    void *(*string_new)(const char *) = dlsym(library, "il2cpp_string_new");
    void *(*object_class)(void *) = dlsym(library, "il2cpp_object_get_class");
    void *(*unbox)(void *) = dlsym(library, "il2cpp_object_unbox");
    void *(*nested_types)(void *, void **) = dlsym(library, "il2cpp_class_get_nested_types");
    const char *(*class_name)(void *) = dlsym(library, "il2cpp_class_get_name");
    void *(*get_field)(void *, const char *) = dlsym(library, "il2cpp_class_get_field_from_name");
    void *(*new_object)(void *) = dlsym(library, "il2cpp_object_new");
    void (*set_field)(void *, void *, void *) = dlsym(library, "il2cpp_field_set_value");
    void (*set_object_field)(void *, void *, void *) = dlsym(library, "il2cpp_field_set_value_object");
    void (*set_static_field)(void *, void *) = dlsym(library, "il2cpp_field_static_set_value");
    const void *(*get_method)(void *, const char *, int) = dlsym(library, "il2cpp_class_get_method_from_name");
    const void *(*return_type)(const void *) = dlsym(library, "il2cpp_method_get_return_type");
    void *(*class_from_type)(const void *) = dlsym(library, "il2cpp_class_from_type");
    if (!octo_image || !string_new || !object_class || !unbox || !nested_types ||
        !class_name || !get_field || !new_object || !set_field || !set_object_field ||
        !set_static_field || !get_method || !return_type || !class_from_type) {
        return 0;
    }
    void *manager_class = get_class(octo_image, "Octo.Data", "DataManager");
    void *octo_class = get_class(octo_image, "Octo", "OctoManager");
    if (!manager_class || !octo_class) {
        return 0;
    }
    void *iterator = 0, *internal_class = 0;
    while ((internal_class = nested_types(octo_class, &iterator))) {
        if (!strcmp(class_name(internal_class), "Internal")) {
            break;
        }
    }
    void *manager_field = internal_class ? get_field(internal_class, "_dataManager") : 0;
    const void *read = get_method(manager_class, "ReadSecureDatabase", 1);
    void *file_class = read ? class_from_type(return_type(read)) : 0;
    const char *catalog_path = battle_path("stage/OctoCatalog.bytes");
    long catalog_size = 0;
    void *bytes = read_managed_bytes(library, catalog_path, &catalog_size);
    void *path = string_new(catalog_path);
    if (!manager_field || !file_class || !bytes || catalog_size <= 0 || !path) {
        return 0;
    }
    /* The host has removed only the encrypted file envelope. Decode the
       catalog through the same closed ProtoBuf reader as ReadSecureDatabase. */
    void *file_args[] = {path, 0};
    void *file = construct(file_class, 2, file_args);
    void *payload_args[] = {bytes};
    /* The constructor builds an Android cache path through JNI. The original
       dictionary and URL builders take the parsed catalog directly. */
    void *version_field = get_field(manager_class, "_version");
    void *path_field = get_field(manager_class, "_dataPath");
    void *manager = new_object(manager_class);
    int cache_version = 903646401;
    if (!manager || !version_field || !path_field) {
        return 0;
    }
    set_field(manager, version_field, &cache_version);
    set_object_field(manager, path_field, path);
    void *database = 0, *revision = 0, *unused = 0;
    if (!file ||
        !call(file_class, "Deserialize", 1, file, payload_args, &database) || !database ||
        !call(object_class(database), "get_revision", 0, database, 0, &revision) || !revision) {
        return 0;
    }
    void *database_args[] = {database};
    void *revision_args[] = {unbox(revision)};
    if (!call(manager_class, "set_Revision", 1, manager, revision_args, &unused) ||
        !call(manager_class, "SetUrls", 1, manager, database_args, &unused) ||
        !call(manager_class, "ConstructDictionary", 1, manager, database_args, &unused)) {
        return 0;
    }
    set_static_field(manager_field, manager);
    char stage_bundle[48];
    int length = snprintf(stage_bundle, sizeof(stage_bundle), "stage_%s", stage_resource);
    if (length < 0 || length >= (int) sizeof(stage_bundle)) {
        return 0;
    }
    void *stage_args[] = {string_new(stage_bundle)};
    void *exists = 0;
    if (!stage_args[0] || !call(octo_class, "IsAssetBundleExists", 1, 0, stage_args, &exists) ||
        !exists || !*(unsigned char *) unbox(exists)) {
        return 0;
    }
    printf("REUSE original_octo_catalog_ready revision %d\n", *(int *) unbox(revision));
    fflush(0);
    return 1;
}
