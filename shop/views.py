import uuid, requests
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q
from django.http import HttpResponse
from django.template.loader import get_template
from xhtml2pdf import pisa
from .models import Product, Category, Order, OrderItem, Review 
from .forms import UserRegisterForm

# --- Product & Home Views ---
def home(request):
    products = Product.objects.all()
    categories = Category.objects.all()
    return render(request, 'shop/home.html', {'products': products, 'categories': categories})

def category_view(request, slug):
    category = get_object_or_404(Category, slug=slug)
    products = Product.objects.filter(category=category)
    categories = Category.objects.all()
    return render(request, 'shop/home.html', {'products': products, 'categories': categories})

def search_view(request):
    query = request.GET.get('q')
    results = Product.objects.filter(Q(name__icontains=query) | Q(description__icontains=query)) if query else []
    return render(request, 'shop/home.html', {'products': results, 'query': query})

def product_detail(request, id):
    product = get_object_or_404(Product, id=id)
    return render(request, 'shop/detail.html', {'product': product})

def terms_conditions(request):
    return render(request, 'shop/terms.html')

# --- Cart Management ---
def add_to_cart(request, product_id):
    cart = request.session.get('cart', {})
    try:
        quantity = int(request.POST.get('quantity', 1))
        if quantity < 1: quantity = 1
    except (ValueError, TypeError):
        quantity = 1
        
    pid_str = str(product_id)
    if pid_str in cart:
        cart[pid_str] += quantity
    else:
        cart[pid_str] = quantity
        
    request.session['cart'] = cart
    return redirect('cart')

def cart_view(request):
    cart = request.session.get('cart', {})
    cart_items = []
    total_price = 0
    for pid, qty in list(cart.items()):
        try:
            product = Product.objects.get(id=pid)
            subtotal = product.price * qty
            total_price += subtotal
            cart_items.append({'product': product, 'quantity': qty, 'subtotal': subtotal})
        except Product.DoesNotExist:
            del cart[pid]
            request.session['cart'] = cart
    return render(request, 'shop/cart.html', {'cart_items': cart_items, 'total_price': total_price})

def update_cart(request, product_id):
    if request.method == 'POST':
        cart = request.session.get('cart', {})
        try:
            quantity = int(request.POST.get('quantity', 1))
            if quantity > 0:
                cart[str(product_id)] = quantity
            else:
                cart.pop(str(product_id), None)
        except (ValueError, TypeError):
            pass
            
        request.session['cart'] = cart
        request.session.modified = True
    return redirect('cart')

def clear_cart(request):
    if 'cart' in request.session: 
        del request.session['cart']
    return redirect('cart')

# --- Checkout & SSLCommerz Payment ---
@login_required
def checkout(request):
    cart = request.session.get('cart', {})
    if not cart: return redirect('home')
    total = sum(get_object_or_404(Product, id=pid).price * qty for pid, qty in cart.items())
    return render(request, 'shop/checkout.html', {'total_price': total})

@login_required
def init_payment(request):
    cart = request.session.get('cart', {})
    if not cart: return redirect('home')
    total_amount = sum(get_object_or_404(Product, id=pid).price * qty for pid, qty in cart.items())

    post_data = {
        'store_id': 'testbox',
        'store_passwd': 'qwerty',
        'total_amount': float(total_amount),
        'currency': 'BDT',
        'tran_id': str(uuid.uuid4())[:10],
        'success_url': "http://127.0.0.1:8000/payment-success/",
        'fail_url': "http://127.0.0.1:8000/payment-fail/",
        'cancel_url': "http://127.0.0.1:8000/payment-cancel/",
        'cus_name': request.user.username,
        'cus_email': request.user.email or "test@test.com",
        'cus_add1': 'Dhaka', 'cus_city': 'Dhaka', 'cus_country': 'Bangladesh', 'cus_phone': '01711111111',
        'shipping_method': 'NO', 'product_name': 'Ecommerce Order', 'product_category': 'General', 'product_profile': 'general'
    }

    url = "https://sandbox.sslcommerz.com/gwprocess/v4/api.php"
    try:
        response = requests.post(url, data=post_data)
        result = response.json()
        if result.get('status') == 'SUCCESS':
            return redirect(result.get('GatewayPageURL'))
        else:
            return render(request, 'shop/payment_failed.html', {'error': result.get('failedreason')})
    except Exception as e:
        return render(request, 'shop/payment_failed.html', {'error': str(e)})

@csrf_exempt
def payment_success(request):
    cart = request.session.get('cart', {})
    order = None

    if cart:
        if request.user.is_authenticated:
            # ১. নতুন অর্ডার তৈরি করা
            order = Order.objects.create(
                user=request.user, 
                total_price=0, 
                is_paid=True
            )
            
            total_amount = 0
            for pid, qty in cart.items():
                try:
                    product = Product.objects.get(id=pid)
                    subtotal = product.price * qty
                    total_amount += subtotal
                    
                    # ২. প্রতিটি প্রোডাক্টকে OrderItem হিসেবে সেভ করা
                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        quantity=qty,
                        price=product.price
                    )
                except Product.DoesNotExist:
                    continue

            # ৩. টোটাল প্রাইজ সেভ করা
            order.total_price = total_amount
            order.save()

            # ৪. কার্ট পুরোপুরি খালি করা (সেশন আপডেট সহ)
            request.session['cart'] = {}
            request.session.modified = True
            if 'cart' in request.session:
                del request.session['cart']
                request.session.modified = True
            
    return render(request, 'shop/payment_success.html', {'order_id': order.id if order else None})

@csrf_exempt
def payment_fail(request):
    return render(request, 'shop/payment_failed.html')

@csrf_exempt
def payment_cancel(request):
    return render(request, 'shop/payment_cancelled.html')

# --- Auth & User Dashboard ---
def register_view(request):
    if request.method == 'POST':
        form = UserRegisterForm(request.POST)
        if form.is_valid(): form.save(); return redirect('login')
    else: form = UserRegisterForm()
    return render(request, 'shop/register.html', {'form': form})

def login_view(request):
    if request.method == 'POST':
        form = AuthenticationForm(data=request.POST)
        if form.is_valid(): login(request, form.get_user()); return redirect('home')
    else: form = AuthenticationForm()
    return render(request, 'shop/login.html', {'form': form})

def logout_view(request):
    logout(request); return redirect('home')

@login_required
def dashboard(request):
    # সব অর্ডার ফিল্টার করে লেটেস্ট গুলো আগে দেখানো
    orders = Order.objects.filter(user=request.user).order_by('-id')
    return render(request, 'shop/dashboard.html', {'orders': orders})

# --- PDF Invoice ---
@login_required
def download_invoice(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)
    template_path = 'shop/invoice_pdf.html'
    context = {'order': order}
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="invoice_{order.id}.pdf"'
    
    template = get_template(template_path)
    html = template.render(context)

    pisa_status = pisa.CreatePDF(html, dest=response)
    if pisa_status.err:
       return HttpResponse('We had some errors <pre>' + html + '</pre>')
    return response

# --- Review ---
@login_required
def add_review(request, product_id):
    if request.method == 'POST':
        product = get_object_or_404(Product, id=product_id)
        Review.objects.create(
            product=product, user=request.user, 
            rating=request.POST.get('rating'), 
            comment=request.POST.get('comment')
        )
    return redirect('product_detail', id=product_id)