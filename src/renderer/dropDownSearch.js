document.addEventListener('DOMContentLoaded', () => {
    const searchButton = document.getElementById('search-button');
    const searchDropDown =document.getElementById('search-area');
    searchButton.addEventListener('click', () => {
        searchDropDown.style.display = searchDropDown.style.display === 'flex' ? 'none' : 'flex';
    });

    window.addEventListener('click', function(event) {
        if (!searchButton.contains(event.target) && !searchDropDown.contains(event.target)) {
            searchDropDown.style.display = 'none';
        }
    });

});