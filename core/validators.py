from django.core.exceptions import ValidationError

MAX_UPLOAD_MB = 10

# Sirf safe document/image/archive types allowed hain (koi .exe, .html, .js, .php nahi)
ALLOWED_ATTACHMENT_EXTENSIONS = [
    'pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp',
    'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
    'txt', 'csv', 'zip',
]


def validate_file_size(uploaded_file):
    if uploaded_file.size > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValidationError(f'File is too large. The maximum size is {MAX_UPLOAD_MB} MB.')
